"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";

function svgNode(tag, attributes = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgText(parent, x, y, value, classes = "", anchor = "start") {
  parent.append(svgNode("text", {x, y, class: classes, "text-anchor": anchor}, value));
}

function svgPath(parent, d, classes = "symbol-body") {
  return parent.appendChild(svgNode("path", {d, class: classes}));
}

function symbol(parent, kind, label, edge, mark, node) {
  switch (kind) {
    case "mux":
      svgPath(parent, "M0 -31 L64 -19 V19 L0 31 Z");
      svgText(parent, 25, 4, "MUX", "pin-name");
      break;
    case "flop":
    case "latch":
      svgPath(parent, "M0 -30 H68 V30 H0 Z");
      if (kind === "flop") {
        svgPath(parent, "M0 14 l8 7 -8 7", "symbol-line");
        if (edge === "NegEdge") parent.append(svgNode("circle", {cx:-4, cy:21, r:4, class:"symbol-body"}));
      }
      if (kind === "latch" && node.enableLow) {
        parent.append(svgNode("circle", {cx:-4, cy:21, r:4, class:"symbol-body"}));
      }
      if (kind === "flop" && node.resetLow) {
        parent.append(svgNode("circle", {cx:-4, cy:4, r:4, class:"symbol-body"}));
      }
      svgText(parent, 61, 4, "Q", "pin-name", "end");
      svgText(parent, 36, -12, kind === "flop" ? "DFF" : "Latch", "pin-name", "middle");
      break;
    case "gate": {
      const gate = label.replace(/^(Logical|Reduction) /, "");
      if (gate === "NOT" || gate === "BUF") {
        svgPath(parent, "M0 -25 L61 0 L0 25 Z");
        if (gate === "NOT") parent.append(svgNode("circle", {cx:66, cy:0, r:5, class:"symbol-body"}));
      } else if (["OR", "NOR", "XOR", "XNOR"].includes(gate)) {
        svgPath(parent, "M0 -27 C32 -27 53 -20 64 0 C53 20 32 27 0 27 Q19 0 0 -27 Z");
        if (gate.includes("X")) svgPath(parent, "M-7 -27 Q12 0 -7 27", "symbol-line");
        if (gate.includes("N")) parent.append(svgNode("circle", {cx:69, cy:0, r:5, class:"symbol-body"}));
      } else if (gate === "AND" || gate === "NAND") {
        svgPath(parent, "M0 -27 H32 A27 27 0 0 1 32 27 H0 Z");
        if (gate === "NAND") parent.append(svgNode("circle", {cx:64, cy:0, r:5, class:"symbol-body"}));
      } else {
        svgPath(parent, "M0 -26 H67 V26 H0 Z");
        svgText(parent, 34, 4, gate, "pin-name", "middle");
      }
      const signs = {AND: "&", OR: "|", XOR: "^", NOT: "~", "Logical AND": "&&", "Logical OR": "||",
        "Logical NOT": "!"};
      if (mark || signs[label]) svgText(parent, 31, 4, mark || signs[label], "pin-name", "middle");
      break;
    }
    case "expression":
      svgPath(parent, `M0 ${-edge} H67 V${edge} H0 Z`);
      svgText(parent, 33, 4, "RTL", "pin-name", "middle");
      break;
    case "operator": {
      parent.append(svgNode("circle", {cx: 34, cy: 0, r: 27, class: "symbol-body operator-body"}));
      svgText(parent, 34, 5, mark || "ƒ", "symbol-sign", "middle");
      break;
    }
  }
}

function outputX(node) {
  if (node.kind === "gate") {
    const gate = node.label.replace(/^(Logical|Reduction) /, "");
    return {AND:59, NAND:69, OR:64, NOR:74, XOR:64, XNOR:74, NOT:71, BUF:61}[gate] || 67;
  }
  return {mux:64, flop:68, latch:68, operator:61, expression:67}[node.kind] || 0;
}

function pinGeometry(node, pin) {
  if (pin === "CLK") return {x: node.edge === "NegEdge" ? -8 : 0, y: 21, clock: true};
  if (node.kind === "mux") {
    if (pin === "S") return {x: 22, y: 27, bottom: true};
    return {x: 0, y: pin === "0" ? -14 : 14};
  }
  if (node.kind === "flop" || node.kind === "latch") {
    if (pin === "ARST") return {x: node.resetLow ? -8 : 0, y: 4};
    if (pin === "EN") return {x: node.enableLow ? -8 : 0, y: 21};
    return {x: 0, y: node.inputs.has("ARST") ? -13 : -5};
  }
  if (node.kind === "expression") {
    return {x:0, y: 24 * (parseInt(pin, 10) - (node.inputs.size + 1) / 2)};
  }
  if (node.kind === "operator") return node.inputs.size === 1 ? {x: 7, y: 0} :
    {x: 10, y: pin === "A" ? -13 : 13};
  if (node.kind === "gate") {
    const gate = node.label.replace(/^(Logical|Reduction) /, "");
    if (node.inputs.size === 1) return {x: gate.includes("OR") ? 9 : 0, y:0};
    return {x: gate.includes("OR") ? (gate.includes("X") ? 0 : 7) : 0, y:pin === "A" ? -15 : 15};
  }
  return {x:0, y:0};
}

function shorten(value, limit = 25) {
  return value.length > limit ? value.slice(0, limit - 1) + "…" : value;
}

function usesBox(node) {
  return BOX_KINDS.includes(node.kind);
}

function pinPosition(node, pin, outgoing) {
  if (node.kind === "bitfields") {
    return outgoing ? {x: node.x + node.width, y: node.y} :
      {x: node.x, y: node.y + TILE * (Number(pin) - (node.fields.length - 1) / 2)};
  }
  if (usesBox(node)) {
    const pins = outgoing ? node.outputs : node.inputs;
    const index = [...pins.keys()].indexOf(pin);
    return {x: node.x + (outgoing ? node.width : 0), y: node.y - node.height / 2 + 48 + index * 24};
  }
  if (["net", "tap"].includes(node.kind)) {
    return {x: node.x + (outgoing ? node.width : 0), y: node.y};
  }
  if (outgoing) return {x: node.x + outputX(node), y: node.y};
  const point = pinGeometry(node, pin);
  return {x: node.x + point.x, y: node.y + point.y, bottom: point.bottom};
}

function routeSchematic(graph, layout, track, chooseWire, signalLabel) {
  const layer = svgNode("g", {class: "wire-layer"});
  const paths = svgNode("g");
  const junctions = svgNode("g");
  const localValues = svgNode("g", {class: "local-values"});
  layer.append(paths, junctions, localValues);
  const route = createWireRouter(graph, layout);
  const groups = new Map();
  for (const edge of graph.edges) {
    if (edge.from.kind === "literal") {
      const pin = pinPosition(edge.to, edge.input, false);
      const key = wireTraceKey(edge);
      const caption = edge.from.label + " to " + edge.to.label + " " + edge.input;
      const x = pin.bottom ? pin.x : pin.x - VALUE_STUB;
      const y = pin.bottom ? pin.y + VALUE_STUB : pin.y;
      const value = drawWire(`M${x} ${y} L${pin.x} ${pin.y}`, edge.clock, caption,
        () => chooseWire(edge.net, key));
      value.classList.add("local-value");
      const wire = value.querySelector("path");
      wire.setAttribute("data-from", edge.from.label);
      wire.setAttribute("data-to", edge.to.label);
      svgText(value, pin.bottom ? x : x - 4, pin.bottom ? y + 12 : y + 3,
        localValueLabel(edge.from.label), "local-value-label", pin.bottom ? "middle" : "end");
      localValues.append(value);
      track(wire, key);
      track(value, key);
      continue;
    }
    const key = edge.from.id + ":" + edge.output;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(edge);
  }
  let id = 0;
  const routes = [...groups.values()].sort((a, b) => Number(b[0].clock) - Number(a[0].clock) || b.length - a.length);
  for (const edges of routes) {
    id++;
    const points = new Map();
    const record = (point, neighbor, edge) => {
      const key = point.x + "," + point.y;
      if (!points.has(key)) points.set(key, {point, neighbors: new Set(), selections: new Set(), clock: false});
      const item = points.get(key);
      item.neighbors.add(neighbor.x + "," + neighbor.y);
      item.selections.add(wireTraceKey(edge));
      item.clock ||= edge.clock;
    };
    for (const edge of edges) {
      const start = pinPosition(edge.from, edge.output, true);
      const end = pinPosition(edge.to, edge.input, false);
      const launch = {x: edge.from.rightChannel, y: snap(start.y)};
      const land = {
        x: edge.to.leftChannel,
        y: end.bottom ? snap(edge.to.y + edge.to.height / 2 + 12) : snap(end.y)
      };
      const key = wireTraceKey(edge);
      const name = edge.net && signalLabel(edge.net);
      const prefix = name && name !== edge.from.label ? name + " · " : "";
      const caption = prefix + edge.from.label + " to " + edge.to.label +
        (edge.input === "IN" ? "" : " " + edge.input);
      const pointsOnGrid = route(launch, land, id);
      const path = [start, {x: launch.x, y: start.y}, ...pointsOnGrid];
      if (end.bottom) path.push({x: end.x, y: land.y});
      else path.push({x: land.x, y: end.y});
      path.push(end);
      const distinct = path.filter((point, i) => !i || path[i - 1].x !== point.x || path[i - 1].y !== point.y);
      const corners = distinct.filter((point, i) => {
        if (i === 0 || i === distinct.length - 1) return true;
        const a = distinct[i - 1], b = distinct[i + 1];
        return !(a.x === point.x && point.x === b.x) && !(a.y === point.y && point.y === b.y);
      });
      const commands = corners.map((p, i) => (i ? "L" : "M") + p.x + " " + p.y).join(" ");
      const connection = drawWire(commands, edge.clock, caption, () => chooseWire(edge.net, key));
      const wire = connection.querySelector("path");
      if (edge.from.bits > 1) wire.classList.add("bus-wire");
      wire.setAttribute("data-from", edge.from.label);
      wire.setAttribute("data-to", edge.to.label);
      paths.append(connection);
      track(wire, key);
      track(connection, key);
      if (edges.length > 1) {
        for (let i = 1; i < pointsOnGrid.length; i++) {
          const a = pointsOnGrid[i - 1], b = pointsOnGrid[i];
          record(a, b, edge);
          record(b, a, edge);
        }
      }
    }
    for (const {point, neighbors, selections, clock} of points.values()) {
      if (neighbors.size < 3) continue;
      const dot = svgNode("circle", {cx: point.x, cy: point.y, r: 3,
        class: "wire-junction" + (clock ? " clock-junction" : "")});
      junctions.append(dot);
      for (const key of selections) track(dot, key);
    }
  }
  return layer;
}

function drawDecisionRows(group, node, top) {
  const badges = {if: "IF", else: "ELSE", case: "CASE", "case z": "CASEZ", "case x": "CASEX",
    "case inside": "CASE INSIDE", match: "MATCH", default: "DEFAULT", rtl: "RTL",
    sweep: "ADDRESS SWEEP", fill: "FILL", set: "SET"};
  for (const [index, row] of node.rows.entries()) {
    const x = node.contentX + row.depth * 13;
    const y = top + 49 + index * DECISION_ROW;
    const line = svgNode("g", {class: "decision-row decision-" + row.kind.replaceAll(" ", "-")});
    if (row.depth) svgPath(line, `M${x-9} ${y-12} v9 h5`, "decision-branch");
    const badge = badges[row.kind];
    let textX = x;
    if (badge) {
      const width = badge.length * 6.2 + 10;
      line.append(svgNode("rect", {x, y: y - 13, width, height: 15, rx: 3, class: "decision-badge"}));
      svgText(line, x + width / 2, y - 2, badge, "decision-badge-text", "middle");
      textX += width + 7;
    }
    svgText(line, textX, y - 1, shorten(row.text, 92), "decision-text");
    if (row.text.length > 92) line.append(svgNode("title", {}, row.text));
    group.append(line);
  }
}

function drawConcatenation(group, node) {
  const join = Math.round(node.width / 2);
  const half = TILE * (node.fields.length - 1) / 2;
  svgText(group, node.width / 2, -node.height / 2 + 13, concatenationLabel(node.fields),
    "concat-label", "middle");
  for (let offset = -half; offset <= half; offset += TILE) {
    svgPath(group, `M0 ${offset} H${join - 6} L${join} ${offset - 4}`, "concat-merge");
  }
  svgPath(group, `M${join} ${-half - 4} V${half - 4} M${join} -4 L${join + 6} 0 H${node.width}`,
    "concat-merge concat-bus");
  if (node.caption) svgText(group, node.width, 15, node.caption, "pin-label", "end");
}

function drawSchematicSymbol(node) {
  const group = svgNode("g", {class: "circuit-node " + node.kind, "data-label": node.label});
  const local = svgNode("g", {transform: `translate(${node.x} ${node.y})`});
  group.append(local);

  if (["net", "tap"].includes(node.kind)) {
    const half = node.height / 2;
    local.append(svgNode("rect", {x: 0, y: -half, width: node.width, height: node.height, rx: 4,
      class: node.kind === "net" ? "net-body" : "terminal-body"}));
    svgText(local, node.width / 2, node.caption ? -2 : 4, shorten(node.label, 27), "net-label", "middle");
    if (node.caption) svgText(local, node.width / 2, 13, node.caption, "pin-label", "middle");
  } else if (node.kind === "bitfields") {
    drawConcatenation(local, node);
  } else if (usesBox(node)) {
    const top = -node.height / 2;
    local.append(svgNode("rect", {x: 0, y: top, width: node.width, height: node.height, rx: 4,
      class: "symbol-body"}));
    svgText(local, 9, top + 16, shorten(node.label, 27), "net-label");
    if (node.caption) svgText(local, 9, top + 31, shorten(node.caption, 31), "pin-label");
    if (node.rows?.length) drawDecisionRows(local, node, top);
    for (const outgoing of [false, true]) {
      const pins = outgoing ? node.outputs : node.inputs;
      for (const [key, label] of pins) {
        const point = pinPosition(node, key, outgoing);
        const x = point.x - node.x;
        const y = point.y - node.y;
        local.append(svgNode("circle", {cx: x, cy: y, r: 2.8, class: "terminal-dot"}));
        svgText(local, x + (outgoing ? -7 : 7), y + 3, shorten(label, outgoing ? 18 : 24),
          "pin-label", outgoing ? "end" : "start");
      }
    }
    if (node.kind === "bank") {
      const center = node.width / 2;
      for (const dy of [-10, 0, 10]) svgPath(local, `M${center - 13} ${dy} h26`, "memory-grid");
      for (const dx of [-10, 0, 10]) svgPath(local, `M${center + dx} -13 v26`, "memory-grid");
    }
  } else {
    symbol(local, node.kind, node.label, node.kind === "expression" ? node.height / 2 - 7 : node.edge, node.symbol, node);
    if (node.bits > 1) {
      svgText(local, 34, -37, busRange(node), "pin-label", "middle");
    }
    const label = node.branch || (node.kind === "gate" && node.symbol
      ? `${node.label} (${node.symbol})` : node.label);
    if (node.kind !== "mux" || node.branch) {
      svgText(local, 34, node.height / 2 + 9, shorten(label, 24), "net-label", "middle");
    }
    if (["flop", "latch", "mux"].includes(node.kind)) {
      for (const pin of node.inputs.keys()) {
        const point = pinGeometry(node, pin);
        const inside = Boolean(node.branch && pin !== "S");
        const x = point.x + (point.bottom || inside ? 6 : -6);
        const y = point.y + (point.bottom ? 12 : inside ? 3 : -4);
        svgText(local, x, y, pin, point.clock ? "clock-ink" : "pin-label",
          point.bottom || inside ? "start" : "end");
      }
    }
  }
  const storage = node.kind === "flop" ? (node.edge === "NegEdge" ? "Falling-edge D flip-flop" : "Rising-edge D flip-flop")
    : node.kind === "latch" ? `D latch · transparent when EN is ${node.enableLow ? "low" : "high"}` : "";
  group.append(svgNode("title", {}, [node.kind === "gate" && node.symbol ? `${node.label} (${node.symbol})` : node.label,
    storage, node.resetValue !== undefined && `Asynchronous reset · active ${node.resetLow ? "low" : "high"} → ${node.resetValue}`,
    node.caption, node.fields && node.fields.map(field => field.range + " ← " + field.text).join("\n"),
    node.processLabel && node.processLabel + " · line " + node.source.line,
    node.branch && `1: ${node.branch} · 0: ${node.fallback}`, node.port?.binding &&
    "Parent connection " + node.port.binding].filter(Boolean).join("\n")));
  return group;
}

function makeInteractive(group, label, action) {
  group.classList.add("interactive-node");
  group.setAttribute("role", "button");
  group.setAttribute("tabindex", "0");
  group.setAttribute("aria-label", label);
  group.addEventListener("click", action);
  group.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    action();
  });
}

function drawWire(commands, clock, caption, choose) {
  const connection = svgNode("g", {class: "wire-connection"});
  makeInteractive(connection, "Trace wire " + caption, choose);
  connection.setAttribute("aria-pressed", "false");
  connection.append(svgNode("title", {}, caption));
  svgPath(connection, commands, clock ? "clock-wire" : "data-wire");
  svgPath(connection, commands, "wire-hit");
  return connection;
}

function drawDataSchematic(scope, signals, signalLabel, selectSignal, openChild) {
  const section = make("section", "schematic-section");
  const header = make("div", "schematic-heading");
  header.append(make("h3", "", "Data schematic"), make("span", "source", signals.size + " signals"));
  section.append(header);

  const graph = buildSchematicGraph(scope, signals, signalLabel);
  if (!graph.nodes.length && !graph.regions.length) {
    section.append(make("p", "empty", "No circuit connections were elaborated in this scope."));
    return {section, highlight() {}};
  }

  const layout = layoutSchematic(graph);
  const zoomBar = make("div", "schematic-tools");
  const minus = make("button", "schematic-tool", "−");
  const plus = make("button", "schematic-tool", "+");
  const fit = make("button", "schematic-tool", "Fit");
  const normal = make("button", "schematic-tool", "100%");
  const readout = make("span", "source");
  minus.setAttribute("aria-label", "Zoom out");
  plus.setAttribute("aria-label", "Zoom in");
  fit.setAttribute("aria-label", "Fit schematic");
  normal.setAttribute("aria-label", "Actual size");
  zoomBar.append(minus, readout, plus, fit, normal);
  header.append(zoomBar);
  if (graph.edges.some(edge => edge.clock)) {
    header.append(make("span", "clock-key", "Clock"));
  }

  const viewport = make("div", "schematic-scroll");
  viewport.tabIndex = 0;
  viewport.setAttribute("aria-label", "Schematic viewport");
  const svg = svgNode("svg", {class: "circuit-svg", viewBox: `0 0 ${layout.width} ${layout.height}`,
    width: layout.width, height: layout.height, role: "group",
    "aria-label": "Connected RTL schematic for " + scope.name});
  const details = make("div", "schematic-source");
  const highlighted = new Map();
  const traceKeys = connectedTraceKeys(graph);
  function track(element, key, style = "net-highlight") {
    if (!highlighted.has(key)) highlighted.set(key, new Map());
    const elements = highlighted.get(key);
    if (!elements.has(element)) elements.set(element, new Set());
    elements.get(element).add(style);
  }

  function markEndpoint(node, pin, outgoing, key, net) {
    const {group, inputs, outputs} = symbols.get(node);
    const pins = outgoing ? outputs : inputs;
    const role = outgoing ? "trace-driver" : "trace-load";
    if (!pins.has(pin)) {
      const point = pinPosition(node, pin, outgoing);
      const marker = svgNode("circle", {cx: point.x, cy: point.y, r: 4,
        class: "trace-pin " + (outgoing ? "driver-pin" : "load-pin")});
      group.append(marker);
      const hit = svgNode("circle", {cx: point.x, cy: point.y, r: 7, class: "pin-hit",
        "data-pin": pin, "data-direction": outgoing ? "output" : "input"});
      hit.addEventListener("click", event => {
        event.stopPropagation();
        selectSignal(net, {key, reveal: false});
      });
      group.append(hit);
      pins.set(pin, marker);
    }
    track(group, key, role);
    track(pins.get(pin), key);
  }

  const outline = 12;
  svg.append(svgNode("rect", {x: outline, y: outline, width: layout.width - 2 * outline,
    height: layout.height - 2 * outline, rx: 12, class: "schematic-outline"}));
  const headings = svgNode("g", {class: "region-headings"});
  for (const region of layout.regions) {
    const process = region.kind === "process";
    const arm = region.kind === "arm";
    const box = svgNode("g", {class: process ? "process-region" : "case-region",
      "data-region": region.kind, "aria-label": region.label});
    if (arm) box.setAttribute("data-color", region.label === "default" ? "default" : region.color % 8);
    box.append(svgNode("rect", {x: region.x, y: region.y, width: region.width, height: region.height,
      rx: 9, class: process ? "process-outline" : arm ? "case-arm-outline" : "case-outline"}));
    const heading = svgNode("g", {class: box.getAttribute("class")});
    if (arm) heading.setAttribute("data-color", box.getAttribute("data-color"));
    const title = svgNode("text", {x: region.x + 14, y: region.y + 23,
      class: process ? "process-region-title" : "case-region-title"}, shorten(region.label, 51));
    if (!process && region.source) {
      makeInteractive(title, "Open RTL for " + region.label, () => openSource(region.source));
    }
    title.append(svgNode("title", {}, region.label + " · line " + region.source.line));
    heading.append(title);
    if (arm && !region.hasContent) {
      svgText(heading, region.x + 14, region.y + 53,
        region.empty ? "No assignments" : "No separate circuit shown", "case-region-note");
    }
    svg.append(box);
    headings.append(heading);
  }
  const wires = routeSchematic(graph, layout, track, (net, key) =>
    selectSignal(net, {key, reveal: false}), signalLabel);
  svg.append(wires);
  const portLeads = svgNode("g", {class: "port-leads"});
  svg.append(portLeads);
  const symbols = new Map();
  for (const node of graph.nodes) {
    if (node.kind === "literal") continue;
    const group = drawSchematicSymbol(node);
    const side = portSide(node);
    if (side) {
      group.setAttribute("data-port-side", side);
      const right = side === "right";
      const outside = right ? layout.width - outline : outline;
      const inside = node.x + (right ? node.width : 0);
      const key = signalTraceKey(node.net);
      const lead = drawWire(`M${outside} ${node.y} H${inside}`, graph.clocks.has(node.net),
        node.port.direction + " port " + node.label, () => selectSignal(node.net, {key, reveal: false}));
      const terminal = svgNode("circle", {cx: outside, cy: node.y, r: 3.6, class: "terminal-dot"});
      lead.append(terminal);
      portLeads.append(lead);
      track(lead, key);
      track(lead.querySelector("path"), key);
      track(terminal, key);
    }
    if (node.net) track(group, signalTraceKey(node.net));
    symbols.set(node, {group, inputs: new Map(), outputs: new Map()});
    if (node.kind === "net") {
      makeInteractive(group, "Trace " + node.label, () => selectSignal(node.net));
      group.setAttribute("aria-pressed", "false");
    } else if (node.child) {
      makeInteractive(group, "Open " + node.label + " " + node.caption, () => openChild(node.child));
    } else if (node.sourceRows?.length || node.sources?.length || node.initializers?.length) {
      makeInteractive(group, "View " + node.label + " RTL sources", () => {
        details.replaceChildren(make("strong", "", node.label));
        const list = make("ul");
        const procedure = (node.sourceRows || []).filter(row => row.source).map(row =>
          ({source: row.source, label: [row.kind.toUpperCase(), row.text].filter(Boolean).join(" ")}));
        const locations = [...(procedure.length ? procedure : node.sources || []), ...(node.initializers || [])];
        for (const item of locations) {
          const row = make("li", "source");
          row.append(make("span", "", item.label), sourceLink(item.source));
          list.append(row);
        }
        details.append(list);
      });
    } else if (node.source) {
      makeInteractive(group, "Open RTL for " + node.label, () => openSource(node.source));
    }
    svg.append(group);
  }
  for (const edge of graph.edges) {
    if (edge.from.kind !== "literal") {
      markEndpoint(edge.from, edge.output, true, wireTraceKey(edge), edge.net);
    }
    markEndpoint(edge.to, edge.input, false, wireTraceKey(edge), edge.net);
  }
  svg.append(headings);
  viewport.append(svg);
  section.append(viewport, details);

  let zoom = 1;
  let selected = "";
  let manuallyZoomed = false;
  function setZoom(value, keepCenter = true) {
    const x = (viewport.scrollLeft + viewport.clientWidth / 2) / zoom;
    const y = (viewport.scrollTop + viewport.clientHeight / 2) / zoom;
    zoom = Math.max(.06, Math.min(2, value));
    svg.setAttribute("width", Math.ceil(layout.width * zoom));
    svg.setAttribute("height", Math.ceil(layout.height * zoom));
    readout.textContent = Math.round(100 * zoom) + "%";
    if (keepCenter) {
      viewport.scrollLeft = x * zoom - viewport.clientWidth / 2;
      viewport.scrollTop = y * zoom - viewport.clientHeight / 2;
    }
  }
  function zoomToFit() {
    const height = parseFloat(getComputedStyle(viewport).maxHeight);
    setZoom(Math.min(1, (viewport.clientWidth - 4) / layout.width, (height - 4) / layout.height), false);
    viewport.scrollTop = viewport.scrollLeft = 0;
  }
  for (const [button, action] of [[minus, () => setZoom(zoom / 1.25)],
    [plus, () => setZoom(zoom * 1.25)], [normal, () => setZoom(1)], [fit, zoomToFit]]) {
    button.addEventListener("click", () => {
      manuallyZoomed = true;
      action();
    });
  }
  requestAnimationFrame(() => {
    if (!viewport.isConnected || manuallyZoomed) return;
    if (scope.children.length || (graph.nodes.length <= 25 && viewport.clientWidth >= 680)) zoomToFit();
    else {
      setZoom(.9, false);
      const region = layout.regions.find(region => region.kind === "case") || layout.regions[0];
      if (region) {
        viewport.scrollTop = Math.max(0, (region.y - 22) * zoom);
        viewport.scrollLeft = Math.max(0, (region.x - 72) * zoom);
      }
    }
  });

  return {section, highlight(net, {key = signalTraceKey(net), reveal = true} = {}) {
    for (const related of traceKeys(selected)) {
      for (const [element, styles] of highlighted.get(related) || []) {
        element.classList.remove(...styles);
        if (element.hasAttribute("aria-pressed")) element.setAttribute("aria-pressed", "false");
      }
    }
    selected = key;
    svg.classList.toggle("has-trace", Boolean(key));
    for (const related of traceKeys(key)) {
      for (const [element, styles] of highlighted.get(related) || []) {
        element.classList.add(...styles);
        if (element.hasAttribute("aria-pressed")) element.setAttribute("aria-pressed", "true");
      }
    }
    const node = reveal && graph.anchors.get(net);
    if (node) {
      const x = (node.x + node.width / 2) * zoom;
      const y = node.y * zoom;
      if (x < viewport.scrollLeft + 28 || x > viewport.scrollLeft + viewport.clientWidth - 28) {
        viewport.scrollLeft = x - viewport.clientWidth / 2;
      }
      if (y < viewport.scrollTop + 24 || y > viewport.scrollTop + viewport.clientHeight - 24) {
        viewport.scrollTop = y - viewport.clientHeight / 2;
      }
    }
  }};
}
