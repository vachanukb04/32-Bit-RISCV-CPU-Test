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
  parent.append(svgNode("path", {d, class: classes}));
}

function symbol(parent, kind, label, edge) {
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
      break;
    }
    case "memory":
      svgPath(parent,"M0 -32 H72 V32 H0 Z");
      for (const y of [-13, 0, 13]) svgPath(parent,"M34 " + y + " h27", "symbol-line");
      for (const x of [37, 49, 61]) svgPath(parent,"M" + x + " -18 v36", "symbol-line");
      break;
    case "operator": {
      const signs = {Adder:"+", Subtractor:"−", Multiplier:"×", Equal:"=",
        "Not equal":"≠", "Less than":"<", "Greater than":">", "Left shift":"≪",
        "Right shift":"≫", "Arithmetic right shift":"≫", Divider:"÷"};
      svgPath(parent, "M0 -27 L25 -16 L53 -27 L71 0 L53 27 L25 16 L0 27 Z");
      svgText(parent, 41, 5, signs[label] || "ƒ", "symbol-sign", "middle");
      break;
    }
  }
}

function outputX(node) {
  if (node.kind === "gate") {
    const gate = node.label.replace(/^(Logical|Reduction) /, "");
    return {AND:59, NAND:69, OR:64, NOR:74, XOR:64, XNOR:74, NOT:71, BUF:61}[gate] || 67;
  }
  return {mux:64, flop:68, latch:68, memory:72, operator:71}[node.kind] || 0;
}

function pinGeometry(node, pin) {
  if (pin === "CLK") return {x: node.edge === "NegEdge" ? -8 : 0, y: 21, clock: true};
  if (node.kind === "mux") {
    if (pin === "S") return {x: 22, y: 27, bottom: true};
    return {x: 0, y: pin === "0" ? -14 : 14};
  }
  if (node.kind === "flop" || node.kind === "latch") return {x:0, y: pin === "D" ? -5 : 21};
  if (node.kind === "memory") return {x:0, y: pin === "ADDR" ? -17 : 0};
  if (node.kind === "operator") return {x:0, y: pin === "A" ? -17 : 17};
  if (node.kind === "gate") {
    const gate = node.label.replace(/^(Logical|Reduction) /, "");
    if (node.pins.length === 1) return {x: gate.includes("OR") ? 9 : 0, y:0};
    return {x: gate.includes("OR") ? (gate.includes("X") ? 0 : 7) : 0, y:pin === "A" ? -15 : 15};
  }
  return {x:0, y:0};
}

function renderSchematic(sheet) {
  const nodes = sheet.nodes;
  const depth = [];
  const rows = new Map();
  const positions = nodes.map((node, index) => {
    const level = node.pins.length ? 1 + Math.max(...node.pins.map(pin => depth[pin.source])) : 0;
    depth.push(level);
    const row = rows.get(level) || 0;
    rows.set(level, row + 1);
    return {x:155 + level * 168, y:52 + row * 83};
  });
  const width = 325 + Math.max(...depth) * 168;
  const height = 27 + Math.max(...rows.values()) * 83;
  const svg = svgNode("svg", {class:"circuit-svg", viewBox:`0 0 ${width} ${height}`, width, height,
    role:"img", "aria-label": "RTL schematic for " + sheet.title});
  const wires = svgNode("g");
  const labels = svgNode("g");
  for (const [i, node] of nodes.entries()) {
    const pos = positions[i];
    for (const [j, pin] of node.pins.entries()) {
      const prev = positions[pin.source];
      const end = pinGeometry(node, pin.pin);
      const startX = prev.x + outputX(nodes[pin.source]);
      const endX = pos.x + end.x;
      const endY = pos.y + end.y;
      const bend = pos.x - 28 - j * 11;
      const commands = end.bottom
        ? `M${startX} ${prev.y} H${bend} V${endY+13} H${endX} V${endY}`
        : `M${startX} ${prev.y} H${bend} V${endY} H${endX}`;
      svgPath(wires, commands, end.clock ? "clock-wire" : "data-wire");
      if (["flop", "latch", "memory", "mux"].includes(node.kind)) {
        const x = end.bottom ? endX + 7 : endX - 7;
        const y = end.bottom ? endY + 14 : endY - 4;
        svgText(labels, x, y, pin.pin, end.clock ? "clock-ink" : "pin-label",
          end.bottom ? "start" : "end");
      }
    }
  }
  svg.append(wires);
  for (const [i, node] of nodes.entries()) {
    const p = positions[i];
    if (node.kind === "terminal" || node.kind === "output") {
      svg.append(svgNode("circle", {cx:p.x, cy:p.y, r:3, class:"terminal-dot"}));
      svgText(svg, p.x + (node.kind === "terminal" ? -8 : 8), p.y - 7, shorten(node.label),
        "net-label", node.kind === "terminal" ? "end" : "start");
      if (node.label.length > 25) svg.append(svgNode("title", {}, node.label));
      continue;
    }
    const group = svgNode("g", {transform:`translate(${p.x} ${p.y})`});
    symbol(group, node.kind, node.label, node.edge);
    if (node.kind === "memory") {
      if (node.write && node.pins.some(pin => pin.pin === "CLK")) {
        svgPath(group, "M0 14 l8 7 -8 7", "symbol-line");
      }
      if (!node.write) svgText(group, 69, -23, "RD", "pin-name", "end");
    }
    if (node.kind !== "mux") svgText(group, 34, 47, shorten(node.label), "net-label", "middle");
    svg.append(group);
  }
  svg.append(labels);
  return svg;
}

function shorten(value, limit = 25) {
  return value.length > limit ? value.slice(0, limit - 1) + "…" : value;
}

function drawClockWiring(node) {
  const groups = new Map();
  for (const use of node.clockUses) {
    const key = use.signal;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(use);
  }
  const section = make("section", "clock-section");
  section.append(make("h3", "", "Clock wiring"));
  for (const [signal, uses] of groups) {
    const port = node.ports.find(port => port.direction === "input" && port.name === signal);
    const left = port ? "Input " + signal : [...uses[0].driverPorts, ...uses[0].driverExpressions].join(", ") ||
      (uses[0].drivers.length ? "Local RTL driver" : "Clock expression");
    const rows = uses.map((_, i) => 39 + i * 37);
    const mid = rows[Math.floor((rows.length - 1) / 2)];
    const height = rows.at(-1) + 33;
    const svg = svgNode("svg", {viewBox:`0 0 680 ${height}`, width:680, height, class:"circuit-svg",
      role:"img", "aria-label": left + " drives " + uses.map(u => u.label).join(", ")});
    svgText(svg, 176, mid - 8, shorten(left), "net-label", "end");
    svgText(svg, 258, mid - 9, shorten(signal), "clock-ink", "middle");
    svg.append(svgNode("circle", {cx:182, cy:mid, r:3, class:"terminal-dot"}));
    svgPath(svg, `M182 ${mid} H341 M341 ${rows[0]} V${rows.at(-1)}`, "clock-wire");
    for (const [i, use] of uses.entries()) {
      const y = rows[i];
      svgPath(svg, `M341 ${y} H420`, "clock-wire");
      svg.append(svgNode("circle", {cx:341, cy:y, r:3, class:"clock-junction"}));
      if (use.edge === "NegEdge") svg.append(svgNode("circle", {cx:418, cy:y, r:3, class:"symbol-body"}));
      if (["PosEdge","NegEdge"].includes(use.edge) && ["CLK","Clock input"].includes(use.pin)) {
        svgPath(svg, `M422 ${y-6} L430 ${y} L422 ${y+6}`, "symbol-line");
      } else {
        svg.append(svgNode("circle", {cx:422, cy:y, r:2, class:"clock-junction"}));
      }
      svgText(svg, 440, y+4, shorten(use.label, 31), "net-label");
      svgText(svg, 648, y+4, use.pin, "pin-label", "end");
    }
    const wrap = make("div", "schematic-scroll");
    wrap.append(svg);
    section.append(wrap);
    if (port?.binding) {
      section.append(make("p", "source", "Parent binding · " + port.binding));
    }
    const sourceLinks = make("div", "clock-sources");
    const addLink = (text, site) => sourceLinks.append(make("span", "source", text), sourceLink(site));
    for (const site of uses[0].drivers) addLink("Driver", site);
    for (const use of uses) addLink(use.label, use.source);
    const details = make("details", "clock-details");
    details.append(make("summary", "", "Clock pins and RTL sources"), sourceLinks);
    section.append(details);
  }
  return section;
}

function drawDataSchematic(sheets) {
  const section = make("section", "schematic-section");
  const header = make("div", "section-head");
  header.append(make("h3", "", "Data schematic"));
  const select = make("select");
  select.setAttribute("aria-label", "Data schematic output");
  for (const [index, sheet] of sheets.entries()) select.append(new Option(sheet.title, index));
  header.append(select);
  const state = sheets.findIndex(sheet => sheet.nodes.some(node => ["flop", "latch"].includes(node.kind)));
  select.value = String(state >= 0 ? state : 0);
  const host = make("div");
  function render() {
    const sheet = sheets[Number(select.value)];
    const wrap = make("div", "schematic-scroll");
    wrap.append(renderSchematic(sheet));
    host.replaceChildren(wrap);
    const details = make("div", "schematic-caption");
    if (sheet.expressionOnly) details.append(make("span", "source", "Assignment operands · enclosing control in source"));
    details.append(sourceLink(sheet.source));
    host.append(details);
  }
  select.addEventListener("change", render);
  render();
  section.append(header, host);
  return section;
}

function structureGlyph(item) {
  const svg = svgNode("svg", {viewBox:"-14 -35 102 82", width:98, height:76,
    "aria-hidden":"true", class:"structure-glyph"});
  symbol(svg, item.kind, item.label, item.detail?.includes("negedge") ? "NegEdge" : "");
  const gate = item.label.replace(/^(Logical|Reduction) /, "");
  const unary = item.kind === "gate" && (["NOT","BUF"].includes(gate) || item.label.startsWith("Reduction "));
  const sequential = ["flop","latch"].includes(item.kind);
  for (const y of unary ? [0] : sequential ? [-5,21] : [-14,14]) {
    svgPath(svg, `M-13 ${y} H0`, item.kind === "flop" && y === 21 ? "clock-wire" : "data-wire");
  }
  if (item.kind === "mux") svgPath(svg,"M22 27 V39","data-wire");
  svgPath(svg,`M${outputX(item)} 0 H86`,"data-wire");
  return svg;
}
