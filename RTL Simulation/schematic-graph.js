"use strict";

const signalTraceKey = net => net ? "net:" + net : "";
const wireTraceKey = edge => signalTraceKey(edge.net) || "wire:" + edge.from.id + ":" + edge.output;

function busRange(signal) {
  return signal.range || (signal.bits > 1 ? `[${signal.bits - 1}:0]` : "");
}

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
  const illustrated = new Map();
  const retained = new Set(scope.schematics.filter(sheet => sheet.nodes.some(shape =>
    shape.kind === "latch" || (shape.kind === "memory" && shape.write)))
    .map(sheet => sourceKey(sheet.processSource || sheet.source)));
  for (const logic of scope.logic) {
    const key = sourceKey(logic.source);
    const outputs = logic.view?.summary?.outputs;
    if (outputs?.length && outputs.every(result => result.circuit) && !retained.has(key)) {
      illustrated.set(key, outputs.map(result => ({
        ...result.circuit, source: logic.source, processLabel: logic.view.header || logic.kind,
        destination: result.net
      })));
    }
  }

  const portsBySignal = new Map();
  for (const port of scope.ports) {
    const peers = portsBySignal.get(port.internal) || [];
    peers.push(port);
    portsBySignal.set(port.internal, peers);
  }
  const portCaption = port => [port.direction, busRange(port)]
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
      anchors.set(net, add(kind, signalLabel(net), {net, port, caption, bits: port?.bits,
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
    if (!net) return {item: add("literal", shape.label, {source, bits: shape.bits})};
    if (shape.label === signalLabel(net) || shape.label.endsWith(" (Q feedback)")) {
      const wire = signalOutput(net);
      setBusMetadata(wire.item, shape);
      return wire;
    }
    const key = net + "\0" + shape.label;
    if (!taps.has(key)) {
      const item = add("tap", shape.label, {net, source, bits: shape.bits});
      const from = signalOutput(net);
      connect(from.item, item, {net, output: from.output});
      taps.set(key, item);
    }
    return {item: taps.get(key), net};
  }

  function feed(wire, item, options = {}) {
    connect(wire.item, item, {output: wire.output, net: wire.net, ...options});
  }

  function setBusMetadata(node, shape) {
    if (node.kind !== "net" || !shape.bits) return;
    node.bits ??= shape.bits;
    if (!shape.range) return;
    node.caption = node.port ? [node.port.direction, shape.range].join(" · ") : shape.range;
  }

  function output(wire, net, label = "", shape) {
    const sink = anchor(net);
    if (shape) setBusMetadata(sink, shape);
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
      const procedure = view?.header || {Always: "always", AlwaysFF: "always_ff", AlwaysComb: "always_comb",
        AlwaysLatch: "always_latch", Initial: "initial"}[kind] || kind;
      const title = {combinational: "Combinational", initialization: "Initialization",
        initial: "Initial process"}[view?.kind] || procedure;
      const caption = site?.line ? [view && procedure, "line " + site.line].filter(Boolean).join(" · ") : "";
      const rows = view?.kind === "combinational" ? undefined : view?.rows;
      const item = add("process", title, {caption, source: site, rows,
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
    const pin = add("net", port.name, {net, port, bits: port.bits, caption: portCaption(port)});
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

  const logicNodes = new Map();
  const sheets = [
    ...scope.schematics.filter(sheet => !illustrated.has(sourceKey(sheet.processSource || sheet.source))),
    ...[...illustrated.values()].flat()
  ];
  for (const sheet of sheets) {
    const site = sheet.processSource || sheet.source;
    const key = sourceKey(site);
    if (views.get(key)?.kind === "initialization") continue;
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
        if (!refs.length && (fallback || sheet.destination)) {
          const literalKey = JSON.stringify([key, shape.label, shape.bits]);
          if (!processLiterals.has(literalKey)) {
            processLiterals.set(literalKey, terminal(shape, sheet.source));
          }
          wire = processLiterals.get(literalKey);
        } else {
          wire = terminal(shape, sheet.source);
        }
        if (fallback) for (const net of refs) fallback.inputs.add(net);
      } else if (shape.kind === "output") {
        const value = pinInput(shape.pins[0]);
        for (const net of sheet.destination ? [sheet.destination] : refs) {
          if (fallback) {
            const key = value.item.id + ":" + value.output;
            const resolvedInside = value.item.kind === "net" && fallback.localReads.has(value.net);
            if (!resolvedInside && !fallback.item.inputs.has(key)) {
              feed(value, fallback.item, {input: key, inputLabel: ""});
            }
            (fallback.item.sources ||= []).push({source: sheet.source, label: sheet.title});
            fallback.writes.add(net);
          } else {
            output(value, net, sheet.destination ? signalLabel(net) : shape.label, sheet.destination && shape);
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
        // Outputs of one combinational process can share the same elaborated subexpression.
        const signature = sheet.destination && JSON.stringify([key, shape.kind, shape.label, fields,
          shape.bits, shape.symbol, shape.branch, shape.fallback, shape.pins.map(pin => {
            const value = pinInput(pin);
            return [pin.pin, value.item.id, value.output];
          })]);
        let item = logicNodes.get(signature);
        if (!item) {
          item = add(shape.kind, shape.label, {
            edge: shape.edge, source: sheet.source, fields, bits: shape.bits, symbol: shape.symbol,
            branch: shape.branch, fallback: shape.fallback, processLabel: sheet.processLabel,
            compactFields: Boolean(fields && sheet.processLabel),
            caption: fields ? busRange(shape) : undefined
          });
          for (const pin of shape.pins) {
            const value = pinInput(pin);
            feed(value, item, {input: pin.pin, inputLabel: fields ?
              (sheet.processLabel ? fields[Number(pin.pin)].range : "") : pin.pin,
              clock: pin.pin === "CLK" || clocks.has(value.net)});
          }
          if (signature) logicNodes.set(signature, item);
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
