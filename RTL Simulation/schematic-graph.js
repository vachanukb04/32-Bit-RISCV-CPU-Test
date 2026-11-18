"use strict";

const signalTraceKey = net => net ? "net:" + net : "";
const wireTraceKey = edge => signalTraceKey(edge.net) || "wire:" + edge.from.id + ":" + edge.output;

function sourceKey(source) {
  return source ? [source.file, source.line, source.column].join(":") : "";
}

function buildSchematicGraph(scope, signals, signalLabel) {
  const nodes = [];
  const edges = [];
  const anchors = new Map();
  const taps = new Map();
  const processes = new Map();
  const processLiterals = new Map();
  const represented = new Set();
  const memories = new Map();
  const clocks = new Set();
  const views = new Map(scope.logic.filter(item => item.view)
    .map(item => [sourceKey(item.source), item.view]));
  const compact = new Set();
  const explicitWiring = new Set();
  for (const sheet of scope.schematics) {
    const key = sourceKey(sheet.processSource || sheet.source);
    if (!views.get(key)?.summary) continue;
    if (sheet.expressionOnly) compact.add(key);
    if (!sheet.expressionOnly || sheet.nodes.some(node => node.kind === "memory")) explicitWiring.add(key);
  }
  for (const key of explicitWiring) compact.delete(key);
  const portsBySignal = new Map();
  for (const port of scope.ports) {
    const peers = portsBySignal.get(port.internal) || [];
    peers.push(port);
    portsBySignal.set(port.internal, peers);
  }
  const portCaption = port => [port.direction, port.bits > 1 ? port.bits + " bits" : ""]
    .filter(Boolean).join(" · ");
  for (const use of scope.clockUses) {
    if (["CLK", "Clock input"].includes(use.pin)) {
      for (const net of use.traceRefs) clocks.add(net);
    }
  }
  for (const item of scope.structures) {
    if (item.kind === "memory") memories.set(item.net, item);
  }

  function add(kind, label, fields = {}) {
    const node = {id: nodes.length, kind, label, inputs: new Map(), outputs: new Map(), ...fields};
    nodes.push(node);
    return node;
  }

  function anchor(net) {
    if (!anchors.has(net)) {
      const memory = memories.get(net);
      const kind = memory ? "bank" : "net";
      const [candidate, other] = portsBySignal.get(net) || [];
      const port = !memory && !other && candidate?.name === signalLabel(net) ? candidate : undefined;
      const caption = memory?.detail.split(" · ")[0] || (port && portCaption(port));
      anchors.set(net, add(kind, signalLabel(net), {net, port, caption,
        sources: memory?.occurrences.map(item => ({source: item.source, label: "Declaration"})) || []}));
    }
    return anchors.get(net);
  }

  function connect(from, to, options = {}) {
    const {input = "IN", output = "OUT", inputLabel = input, outputLabel = output,
      net = "", clock = clocks.has(net)} = options;
    from.outputs.set(output, outputLabel);
    to.inputs.set(input, inputLabel);
    edges.push({from, to, output, input, net, clock});
  }

  function signalOutput(net) {
    const item = anchor(net);
    return {item, output: item.kind === "bank" ? "VALUE" : "OUT", net};
  }

  function terminal(shape, source) {
    const [net] = shape.nets || [];
    if (!net) return {item: add("literal", shape.label, {source})};
    if (shape.label === signalLabel(net) || shape.label.endsWith(" (Q feedback)")) return signalOutput(net);
    const key = net + "\0" + shape.label;
    if (!taps.has(key)) {
      const item = add("tap", shape.label, {net, source});
      const from = signalOutput(net);
      connect(from.item, item, {net, output: from.output});
      taps.set(key, item);
    }
    return {item: taps.get(key), net};
  }

  function feed(wire, item, options = {}) {
    connect(wire.item, item, {output: wire.output, net: wire.net, ...options});
  }

  function output(wire, net, label = "") {
    const sink = anchor(net);
    if (wire.item !== sink) {
      const slice = label && label !== signalLabel(net) && !label.includes("..") ? label : "";
      const input = sink.kind === "bank" ? "WRITE" : "IN";
      if (slice) {
        const tap = add("tap", slice, {net});
        feed(wire, tap);
        connect(tap, sink, {net, input, inputLabel: sink.kind === "bank" ? "Write" : input});
      } else {
        feed(wire, sink, {net, input, inputLabel: sink.kind === "bank" ? "Write" : input});
      }
    }
  }

  function process(site, kind) {
    const key = sourceKey(site);
    if (!processes.has(key)) {
      const view = views.get(key);
      const procedure = {Always: "always", AlwaysFF: "always_ff", AlwaysComb: "always_comb",
        AlwaysLatch: "always_latch", Initial: "initial"}[kind] || kind;
      const title = {combinational: "Combinational", initialization: "Initialization",
        initial: "Initial process"}[view?.kind] || procedure;
      const caption = site?.line ? [view && procedure, "line " + site.line].filter(Boolean).join(" · ") : "";
      const summary = compact.has(key) ? view.summary : undefined;
      const rows = view?.kind === "combinational" ? undefined : view?.rows;
      const item = add("process", title, {caption, source: site, rows, summary,
        sourceRows: view?.rows});
      processes.set(key, {item, inputs: new Set(view?.localReads || []),
        localReads: new Set(view?.localReads || []), writes: new Set()});
    }
    return processes.get(key);
  }

  for (const net of signals.keys()) anchor(net);
  for (const port of scope.ports) {
    const net = port.internal;
    const internal = anchor(net);
    if (internal.port === port) continue;
    const pin = add("net", port.name, {net, port, caption: portCaption(port)});
    if (port.direction === "output") feed(signalOutput(net), pin);
    else connect(pin, internal, {net});
  }
  const clockPins = new Set(scope.clockUses.filter(use => use.pin === "Clock input").map(use => use.label));
  for (const child of scope.children) {
    const item = add("instance", child.name, {caption: child.module, source: child.source, child});
    for (const port of child.ports) {
      const pin = port.name;
      const label = pin + (port.bits > 1 ? " [" + port.bits + "]" : "");
      if (port.direction === "input" || port.direction === "inout") {
        item.inputs.set(pin, label);
        if (port.binding || port.refs.length) {
          let wire;
          if (port.refs.length === 1) {
            wire = terminal({nets: port.refs, label: port.binding || signalLabel(port.refs[0])}, port.bindingSource);
          } else if (port.refs.length > 1) {
            const binding = add("binding", port.binding || pin, {source: port.bindingSource});
            for (const [index, net] of port.refs.entries()) {
              feed(signalOutput(net), binding, {input: String(index), inputLabel: signalLabel(net)});
            }
            wire = {item: binding};
          } else {
            wire = {item: add("literal", port.binding, {source: port.bindingSource})};
          }
          feed(wire, item, {input: pin, inputLabel: label,
            clock: clockPins.has(child.name + "." + pin) || clocks.has(wire.net)});
        }
      }
      if (port.direction === "output" || port.direction === "inout") {
        item.outputs.set(pin, label);
        for (const net of port.drives) output({item, output: pin}, net, port.binding);
        item.outputs.set(pin, label);
      }
    }
  }

  for (const sheet of scope.schematics) {
    const site = sheet.processSource || sheet.source;
    const key = sourceKey(site);
    if (views.get(key)?.kind === "initialization" || compact.has(key)) continue;
    const root = sheet.nodes.at(-1);
    if (sheet.expressionOnly && views.get(key)?.kind === "combinational" && root.kind === "output" &&
        sheet.nodes.slice(0, -1).every(node => !node.nets?.length && node.kind !== "memory")) {
      continue;
    }
    represented.add(key);
    const fallback = sheet.expressionOnly && process(site, sheet.processKind);
    if (fallback && sheet.processKind === "Initial" && root.kind === "memory" && root.write &&
        sheet.nodes.slice(0, -1).every(shape => shape.kind === "terminal" && !shape.nets?.length)) {
      const bank = anchor(root.nets[0]);
      (bank.initializers ||= []).push({source: sheet.source, label: root.label + "[" +
        sheet.nodes[root.pins.find(pin => pin.pin === "ADDR").source].label + "] = " +
        sheet.nodes[root.pins.find(pin => pin.pin === "WD").source].label});
      fallback.writes.add(root.nets[0]);
      continue;
    }
    const wires = [];
    for (const shape of sheet.nodes) {
      const pinInput = pin => wires[pin.source];
      const refs = shape.nets || [];
      let wire;
      if (shape.kind === "terminal") {
        if (fallback && !refs.length) {
          const key = fallback.item.id + ":" + shape.label;
          if (!processLiterals.has(key)) processLiterals.set(key, terminal(shape, sheet.source));
          wire = processLiterals.get(key);
        } else {
          wire = terminal(shape, sheet.source);
        }
        if (fallback) for (const net of refs) fallback.inputs.add(net);
      } else if (shape.kind === "output") {
        const value = pinInput(shape.pins[0]);
        for (const net of refs) {
          if (fallback) {
            const key = value.item.id + ":" + value.output;
            const resolvedInside = value.item.kind === "net" && fallback.localReads.has(value.net);
            if (!resolvedInside && !fallback.item.inputs.has(key)) {
              feed(value, fallback.item, {input: key, inputLabel: ""});
            }
            (fallback.item.sources ||= []).push({source: sheet.source, label: sheet.title});
            fallback.writes.add(net);
          } else {
            output(value, net, shape.label);
          }
        }
      } else if (shape.kind === "memory") {
        const [net] = refs;
        const bank = anchor(net);
        bank.kind = "bank";
        const countKey = shape.write ? "writeCount" : "readCount";
        const index = bank[countKey] = (bank[countKey] || 0) + 1;
        const prefix = shape.write ? "W" : "R";
        for (const pin of shape.pins) {
          const name = pin.pin === "ADDR" ? "A" : pin.pin === "WD" ? "D" : pin.pin;
          const label = prefix + index + " " + name;
          feed(pinInput(pin), bank, {input: label, inputLabel: label, clock: pin.pin === "CLK"});
        }
        bank.sources.push({source: sheet.source, label: (shape.write ? "Write" : "Read") + " " + index});
        if (shape.write && fallback) fallback.writes.add(net);
        wire = {item: bank, output: "Q" + index, net};
      } else {
        const fields = shape.fields;
        const item = add(shape.kind, shape.label, {
          edge: shape.edge, source: sheet.source, fields,
          caption: fields ? "[" + (shape.bits - 1) + ":0] · MSB to LSB" : undefined
        });
        for (const pin of shape.pins) {
          const value = pinInput(pin);
          feed(value, item, {input: pin.pin, inputLabel: fields ? "" : pin.pin,
            clock: pin.pin === "CLK" || clocks.has(value.net)});
        }
        wire = {item};
      }
      wires.push(wire);
    }
  }

  for (const logic of scope.logic) {
    const key = sourceKey(logic.source);
    if (represented.has(key) && !processes.has(key)) continue;
    if (!logic.inputs.length && !logic.outputs.length && !processes.has(key)) continue;
    const entry = process(logic.source, logic.kind);
    if (entry.item.summary) {
      const summary = entry.item.summary;
      const controls = new Set(summary.controls);
      const data = new Set(summary.dataInputs);
      for (const net of summary.inputs) {
        const label = signalLabel(net);
        const role = controls.has(net) && !data.has(net) ? "Control" : "Data";
        feed(signalOutput(net), entry.item, {input: net, inputLabel: role + " · " + label});
      }
      for (const result of summary.outputs) {
        const item = anchor(result.net);
        connect(entry.item, item, {net: result.net, output: result.net,
          outputLabel: [result.name, result.range].filter(Boolean).join(" ")});
      }
      continue;
    }
    if (logic.view?.kind === "initialization") {
      for (const [net, caption] of Object.entries(logic.view.outputs)) {
        const node = anchor(net);
        const index = logic.view.indices[net];
        if (index) node.caption = "Memory index · final " + index.final;
        const bank = node.kind === "bank";
        connect(entry.item, node, {net, output: net, outputLabel: caption,
          input: bank ? "INIT" : "IN", inputLabel: bank ? "Initialize" : ""});
      }
      continue;
    }
    for (const net of logic.inputs) {
      if (entry.inputs.has(net)) continue;
      feed(signalOutput(net), entry.item, {input: net, inputLabel: signalLabel(net)});
    }
    for (const net of new Set([...logic.outputs, ...entry.writes])) {
      const item = anchor(net);
      if (item.kind === "bank") {
        const initialize = logic.kind === "Initial";
        const label = initialize ? "Initialize" : "Write control";
        connect(entry.item, item, {net, input: label + key, inputLabel: label,
          output: net, outputLabel: initialize ? "Init" : signalLabel(net)});
      } else {
        connect(entry.item, item, {net, output: net, outputLabel: signalLabel(net)});
      }
    }
  }
  return {nodes, edges, anchors, clocks};
}
