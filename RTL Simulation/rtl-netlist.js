const operators = {
  $add: ["Adder", "+"], $sub: ["Subtract", "−"], $mul: ["Multiply", "×"], $div: ["Divide", "/"],
  $mod: ["Remainder", "%"], $eq: ["Equal", "=="], $eqx: ["Case equality", "==="], $ne: ["Not equal", "!="],
  $nex: ["Case inequality", "!=="], $lt: ["Less than", "<"], $le: ["At most", "≤"], $gt: ["Greater than", ">"],
  $ge: ["At least", "≥"], $shl: ["Shift left", "<<"], $shr: ["Shift right", ">>"],
  $sshl: ["Arithmetic shift left", "<<<"], $sshr: ["Arithmetic shift right", ">>>"], $neg: ["Negate", "−"],
};
const gates = {
  $and: "AND", $or: "OR", $xor: "XOR", $xnor: "XNOR", $not: "NOT",
  $logic_and: "Logical AND", $logic_or: "Logical OR", $logic_not: "Logical NOT",
  $reduce_and: "Reduction AND", $reduce_or: "Reduction OR", $reduce_xor: "Reduction XOR",
  $reduce_xnor: "Reduction XNOR", $reduce_bool: "Reduction OR",
};
const literal = bits => bits.length + "'b" + [...bits].reverse().join("");

function location(attribute, sources, fallback) {
  for (const item of (attribute || "").split("|")) {
    const match = /^(.*?):(\d+)(?:\.(\d+))?/.exec(item);
    const file = match?.[1].replace(/^\.\//, "");
    if (file && Object.hasOwn(sources, file)) {
      return {file, line: Number(match[2]), column: Number(match[3] || 1)};
    }
  }
  return fallback;
}

function wires(path, ports, netnames, cells) {
  const owners = new Map();
  const nets = new Set();
  const aliases = [];

  function register(name, wire, alias = false) {
    const {bits, offset = 0, upto = 0} = wire;
    const net = path + "." + name;
    if (bits.length && new Set(bits).size === bits.length &&
        bits.every(bit => typeof bit === "number" && !owners.has(bit))) {
      const bus = {net, name, width: bits.length, offset, upto};
      bits.forEach((bit, index) => owners.set(bit, {bus, index}));
      nets.add(net);
    } else if (alias) {
      nets.add(net);
      aliases.push({net, bits, name});
    }
  }

  const userPorts = Object.entries(ports).sort(([, a], [, b]) => (a.direction === "output") - (b.direction === "output"));
  for (const [name, port] of userPorts) register(name, port, true);
  for (const [name, wire] of Object.entries(netnames)) {
    if (!wire.hide_name && !Object.hasOwn(ports, name)) register(name, wire, true);
  }
  for (const cell of Object.values(cells)) {
    for (const bits of Object.values(cell.connections)) {
      const missing = bits.filter(bit => typeof bit === "number" && !owners.has(bit));
      if (missing.length) register("$wire" + missing[0], {bits: [...new Set(missing)]});
    }
  }
  for (const {bits} of aliases) {
    for (const bit of bits) {
      if (typeof bit === "number" && !owners.has(bit)) register("$wire" + bit, {bits: [bit]});
    }
  }

  function parts(bits) {
    const groups = [];
    for (const bit of bits) {
      const owner = owners.get(bit);
      const previous = groups.at(-1);
      if (owner && previous?.bus === owner.bus && previous.end + 1 === owner.index) {
        previous.end = owner.index;
        previous.bits++;
      } else if (!owner && previous?.constant) {
        previous.constant.push(bit);
        previous.bits++;
      } else {
        groups.push(owner ? {bus: owner.bus, start: owner.index, end: owner.index, bits: 1}
          : {constant: [bit], bits: 1});
      }
    }
    return groups.map(({bus, start, end, constant, bits: width}) => {
      if (!bus) return {label: literal(constant), bits: width, nets: []};
      const index = at => bus.offset + (bus.upto ? bus.width - 1 - at : at);
      const range = width === 1 ? "[" + index(start) + "]" : "[" + index(end) + ":" + index(start) + "]";
      const complete = width === bus.width;
      const label = bus.name + (complete ? "" : range);
      const net = bus.net;
      return {label, bits: width, range: complete && width > 1 ? range : "", nets: [net]};
    });
  }

  function input(bits, nodes) {
    const items = parts(bits).reverse();
    if (!items.length) items.push({label: "Unconnected", bits: 0, nets: []});
    const pins = items.map(item => {
      nodes.push({kind: "terminal", ...item});
      return nodes.length - 1;
    });
    if (pins.length === 1) return pins[0];
    let high = bits.length - 1;
    const fields = items.map(item => {
      const low = high - item.bits + 1;
      const field = {text: item.label, range: low === high ? "[" + high + "]" : "[" + high + ":" + low + "]"};
      high = low - 1;
      return field;
    });
    nodes.push({kind: "bitfields", label: "Concatenation", fields, bits: bits.length,
      pins: pins.map((source, i) => ({pin: String(i), source}))});
    return nodes.length - 1;
  }

  function binding(bits) {
    const groups = parts(bits).reverse();
    const labels = groups.map(group => group.label);
    return {binding: labels.length > 1 ? "{" + labels.join(", ") + "}" : labels[0] || "",
      refs: [...new Set(groups.flatMap(group => group.nets))]};
  }

  return {nets, aliases, parts, input, binding};
}

function circuitShape(cell, output, pins, bits) {
  const {type, parameters = {}} = cell;
  if (gates[type]) return {kind: "gate", label: gates[type], bits, pins};
  if (operators[type]) {
    const [label, symbol] = operators[type];
    return {kind: "operator", label, symbol, bits, pins};
  }
  if (type === "$mux") return {kind: "mux", label: "MUX", bits,
    pins: pins.map(({pin, source}) => ({pin: {A: "0", B: "1", S: "S"}[pin], source}))};
  if (type === "$dff" || type === "$adff") {
    return {kind: "flop", label: "D flip-flop", bits, pins,
      edge: Number(parameters.CLK_POLARITY) ? "PosEdge" : "NegEdge",
      resetLow: type === "$adff" && !Number(parameters.ARST_POLARITY),
      resetValue: type === "$adff" ? literal([...parameters.ARST_VALUE].reverse()) : undefined};
  }
  if (type === "$dlatch") return {kind: "latch", label: "Latch", bits, pins,
    enableLow: !Number(parameters.EN_POLARITY)};
  return {kind: "expression", label: type.replace(/^\$/, "") + " · " + output, bits,
    pins: pins.map(({pin, source}, i) => ({pin: String(i + 1), source, label: pin}))};
}

export function elaborateNetlist(netlist, top, selectedFile, sources, diagnostics = "") {
  const modules = netlist.modules;
  const defaultSource = {file: selectedFile, line: 1, column: 1};
  let instances = 0;
  let cells = 0;
  function instance(type, name, path, parentBindings = {}, parentCell) {
    const module = modules[type];
    const ports = module.ports || {};
    const netnames = module.netnames || {};
    const moduleCells = module.cells || {};
    cells += Object.keys(moduleCells).length;
    if (++instances > 200 || cells > 1500) throw new Error("This hierarchy is too large to draw. Select a smaller module.");
    const source = location(module.attributes?.src, sources, defaultSource);
    const wire = wires(path, ports, netnames, moduleCells);
    const schematics = [];
    const clockUses = [];
    const children = [];

    for (const {net, name: alias, bits} of wire.aliases) {
      const nodes = [];
      const pin = wire.input(bits, nodes);
      nodes.push({kind: "output", label: alias, nets: [net], pins: [{pin: "IN", source: pin}], bits: bits.length});
      schematics.push({source, title: alias, nodes});
    }
    for (const [childName, cell] of Object.entries(moduleCells)) {
      const site = location(cell.attributes?.src, sources, source);
      if (Object.hasOwn(modules, cell.type)) {
        const bindings = Object.fromEntries(Object.entries(cell.connections).map(([pin, bits]) => [pin, wire.binding(bits)]));
        children.push(instance(cell.type, childName, path + "." + childName, bindings, site));
        continue;
      }
      for (const [output, bits] of Object.entries(cell.connections)) {
        if (cell.port_directions?.[output] !== "output") continue;
        const targets = wire.parts(bits).filter(part => part.nets.length);
        if (!targets.length) continue;
        const nodes = [];
        const pins = [];
        for (const [pin, inputs] of Object.entries(cell.connections)) {
          if (cell.port_directions?.[pin] === "output" || !inputs.length) continue;
          const source = wire.input(inputs, nodes);
          pins.push({pin, source});
          if (pin === "CLK" && /^\$(?:adff|dff)$/.test(cell.type)) {
            const refs = wire.binding(inputs).refs;
            clockUses.push({pin, label: childName, refs, traceRefs: refs});
          }
        }
        const driver = nodes.length;
        nodes.push(circuitShape(cell, output, pins, bits.length));
        for (const target of targets) {
          nodes.push({kind: "output", ...target, pins: [{pin: "IN", source: driver}]});
        }
        schematics.push({source: site, title: childName, nodes});
      }
    }
    return {name, module: module.attributes?.hdlname?.trim().split(/\s+/)[0] || type, path, source, children,
      nets: [...wire.nets], clockUses, schematics, logic: [], structures: [], parameters: [],
      ports: Object.entries(ports).map(([name, port]) => {
        const {binding = "", refs = []} = parentBindings[name] || {};
        const {bits, offset = 0, upto = 0, direction} = port;
        const range = bits.length > 1 ? "[" + (upto ? offset : offset + bits.length - 1) + ":" +
          (upto ? offset + bits.length - 1 : offset) + "]" : "";
        return {name, internal: path + "." + name, bits: bits.length, range, direction,
          binding, refs, drives: direction === "output" || direction === "inout" ? refs : [], bindingSource: parentCell};
      })};
  }
  if (!Object.hasOwn(modules, top)) throw new Error("The compiler did not produce the selected module.");
  const files = Object.keys(sources).filter(file => /\.(?:v|sv)$/i.test(file)).sort();
  const headers = Object.keys(sources).filter(file => /\.(?:vh|svh)$/i.test(file)).sort();
  return {top, engine: "Yosys", files, headers, includeDirs: [...new Set(headers.map(file => file.split("/").slice(0, -1).join("/") || "."))],
    filelist: files.map(file => "./" + file).join("\n") + "\n", sourceText: sources, diagnostics,
    issues: [], hierarchy: instance(top, top, top)};
}
