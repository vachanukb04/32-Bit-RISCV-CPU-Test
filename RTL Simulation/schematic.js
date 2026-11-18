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
    case "expression":
      svgPath(parent, `M0 ${-edge} H67 V${edge} H0 Z`);
      svgText(parent, 33, 4, "RTL", "pin-name", "middle");
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
  return {mux:64, flop:68, latch:68, operator:71, expression:67}[node.kind] || 0;
}

function pinGeometry(node, pin) {
  if (pin === "CLK") return {x: node.edge === "NegEdge" ? -8 : 0, y: 21, clock: true};
  if (node.kind === "mux") {
    if (pin === "S") return {x: 22, y: 27, bottom: true};
    return {x: 0, y: pin === "0" ? -14 : 14};
  }
  if (node.kind === "flop" || node.kind === "latch") return {x:0, y: pin === "D" ? -5 : 21};
  if (node.kind === "expression") {
    return {x:0, y: 24 * (parseInt(pin, 10) - (node.inputs.size + 1) / 2)};
  }
  if (node.kind === "operator") return {x:0, y: pin === "A" ? -17 : 17};
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
  return ["process", "bank", "instance", "binding"].includes(node.kind);
}

function pinPosition(node, pin, outgoing) {
  if (usesBox(node)) {
    const pins = outgoing ? node.outputs : node.inputs;
    const index = [...pins.keys()].indexOf(pin);
    return {x: node.x + (outgoing ? node.width : 0), y: node.y - node.height / 2 + 48 + index * 24};
  }
  if (["net", "tap", "literal"].includes(node.kind)) {
    return {x: node.x + (outgoing ? node.width : 0), y: node.y};
  }
  if (outgoing) return {x: node.x + outputX(node), y: node.y};
  const point = pinGeometry(node, pin);
  return {x: node.x + point.x, y: node.y + point.y, bottom: point.bottom};
}

function routeSchematic(graph, layout, track) {
  const layer = svgNode("g", {class: "wire-layer"});
  const paths = svgNode("g");
  const junctions = svgNode("g");
  layer.append(paths, junctions);
  const route = createWireRouter(graph, layout);
  const groups = new Map();
  for (const edge of graph.edges) {
    const key = edge.from.id + ":" + edge.output;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(edge);
  }
  let id = 0;
  for (const edges of groups.values()) {
    id++;
    const points = new Map();
    const record = (point, neighbor, edge) => {
      const key = point.x + "," + point.y;
      if (!points.has(key)) points.set(key, {point, neighbors: new Set(), nets: new Set(), clock: false});
      const item = points.get(key);
      item.neighbors.add(neighbor.x + "," + neighbor.y);
      if (edge.net) item.nets.add(edge.net);
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
      const wire = svgPath(paths, commands, edge.clock ? "clock-wire" : "data-wire");
      wire.setAttribute("data-from", edge.from.label);
      wire.setAttribute("data-to", edge.to.label);
      if (edge.net) track(wire, edge.net);
      if (edges.length > 1) {
        for (let i = 1; i < pointsOnGrid.length; i++) {
          const a = pointsOnGrid[i - 1], b = pointsOnGrid[i];
          record(a, b, edge);
          record(b, a, edge);
        }
      }
    }
    for (const {point, neighbors, nets, clock} of points.values()) {
      if (neighbors.size < 3) continue;
      const dot = svgNode("circle", {cx: point.x, cy: point.y, r: 3,
        class: "wire-junction" + (clock ? " clock-junction" : "")});
      junctions.append(dot);
      for (const net of nets) track(dot, net);
    }
  }
  return layer;
}

function drawSchematicSymbol(node) {
  const group = svgNode("g", {class: "circuit-node " + node.kind, "data-label": node.label});
  const local = svgNode("g", {transform: `translate(${node.x} ${node.y})`});
  group.append(local);

  if (["net", "literal", "tap"].includes(node.kind)) {
    const half = node.height / 2;
    local.append(svgNode("rect", {x: 0, y: -half, width: node.width, height: node.height, rx: 4,
      class: node.kind === "net" ? "net-body" : "terminal-body"}));
    svgText(local, node.width / 2, node.caption ? -2 : 4, shorten(node.label, 27), "net-label", "middle");
    if (node.caption) svgText(local, node.width / 2, 13, node.caption, "pin-label", "middle");
  } else if (usesBox(node)) {
    const top = -node.height / 2;
    local.append(svgNode("rect", {x: 0, y: top, width: node.width, height: node.height, rx: 4,
      class: "symbol-body"}));
    svgText(local, 9, top + 16, shorten(node.label, 27), "net-label");
    if (node.caption) svgText(local, 9, top + 31, shorten(node.caption, 31), "pin-label");
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
    symbol(local, node.kind, node.label, node.kind === "expression" ? node.height / 2 - 7 : node.edge);
    if (node.kind !== "mux") svgText(local, 34, node.height / 2 + 9, shorten(node.label, 21), "net-label", "middle");
    if (["flop", "latch", "mux"].includes(node.kind)) {
      for (const pin of node.inputs.keys()) {
        const point = pinGeometry(node, pin);
        const x = point.bottom ? point.x + 6 : point.x - 6;
        const y = point.bottom ? point.y + 12 : point.y - 4;
        svgText(local, x, y, pin, point.clock ? "clock-ink" : "pin-label", point.bottom ? "start" : "end");
      }
    }
  }
  group.append(svgNode("title", {}, [node.label, node.caption, node.port?.binding &&
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

function drawDataSchematic(scope, signals, signalLabel, selectSignal, openChild) {
  const section = make("section", "schematic-section");
  const header = make("div", "schematic-heading");
  header.append(make("h3", "", "Data schematic"), make("span", "source", signals.size + " signals"));
  section.append(header);

  const graph = buildSchematicGraph(scope, signals, signalLabel);
  if (!graph.nodes.length) {
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
  function track(element, net) {
    if (!highlighted.has(net)) highlighted.set(net, []);
    highlighted.get(net).push(element);
  }

  svg.append(routeSchematic(graph, layout, track));
  for (const node of graph.nodes) {
    const group = drawSchematicSymbol(node);
    if (node.net) track(group, node.net);
    if (node.kind === "net") {
      makeInteractive(group, "Trace " + node.label, () => selectSignal(node.net));
      group.setAttribute("aria-pressed", "false");
    } else if (node.child) {
      makeInteractive(group, "Open " + node.label + " " + node.caption, () => openChild(node.child));
    } else if (node.sources?.length || node.initializers?.length) {
      makeInteractive(group, "View " + node.label + " RTL sources", () => {
        details.replaceChildren(make("strong", "", node.label));
        const list = make("ul");
        for (const item of [...(node.sources || []), ...(node.initializers || [])]) {
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
    if (graph.nodes.length <= 25 && viewport.clientWidth >= 680) zoomToFit();
    else setZoom(.9, false);
  });

  return {section, highlight(net) {
    for (const element of highlighted.get(selected) || []) {
      element.classList.remove("net-highlight");
      if (element.hasAttribute("aria-pressed")) element.setAttribute("aria-pressed", "false");
    }
    selected = net;
    svg.classList.toggle("has-trace", Boolean(net));
    for (const element of highlighted.get(net) || []) {
      element.classList.add("net-highlight");
      if (element.hasAttribute("aria-pressed")) element.setAttribute("aria-pressed", "true");
    }
    const node = graph.anchors.get(net);
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
