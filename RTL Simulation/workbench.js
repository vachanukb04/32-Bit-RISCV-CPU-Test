"use strict";

const byId = id => document.getElementById(id);
const make = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};

let candidates = [];
let result;
let chain = [];
const expanded = new Set();

function status(message, error = false) {
  byId("status").textContent = message;
  byId("status").dataset.error = error;
}

function inputs() {
  return {project: byId("project").value, scope: byId("scope").value};
}

async function request(kind, data) {
  const reply = await fetch("/api/" + kind, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data),
  });
  const payload = await reply.json();
  if (!reply.ok) throw new Error(payload.error || reply.statusText);
  return payload;
}

function busy(value) {
  for (const item of document.querySelectorAll("form input, form select, form button")) {
    item.disabled = value;
  }
  if (!value) {
    byId("top").disabled = candidates.length === 0;
    byId("compile-button").disabled = candidates.length === 0;
  }
}

function resetDisplay() {
  result = undefined;
  chain = [];
  expanded.clear();
  byId("tree").replaceChildren(make("p", "empty", "Elaborated instances appear here."));
  byId("diagram").replaceChildren(make("div", "empty stage-empty", "Select a top module to inspect its blocks and signals."));
  for (const id of ["instance-count", "crumbs", "inspector", "sources", "file-count", "includes"]) {
    byId(id).replaceChildren();
  }
  byId("diagnostics").textContent = "No compile has been run.";
  byId("download").disabled = true;
  byId("diagnostic-panel").open = false;
  byId("file-panel").open = false;
}

byId("scan").addEventListener("submit", async event => {
  event.preventDefault();
  resetDisplay();
  candidates = [];
  busy(true);
  byId("scan-summary").textContent = "";
  byId("top").replaceChildren(new Option("Select a top module"));
  status("Scanning source files…");
  try {
    const scan = await request("scan", inputs());
    candidates = scan.tops;
    const roots = make("optgroup");
    roots.label = "Uninstantiated module candidates";
    const remaining = make("optgroup");
    remaining.label = "All other modules";
    for (const [index, item] of candidates.entries()) {
      const option = new Option(item.name + " — " + item.file, String(index));
      (item.rootCandidate ? roots : remaining).append(option);
    }
    if (roots.children.length) byId("top").append(roots);
    if (remaining.children.length) byId("top").append(remaining);
    byId("directories").replaceChildren(...scan.directories.map(path => new Option(path)));
    byId("scan-summary").textContent = scan.count + " RTL files found";
    status("Select a module and its source path.");
  } catch (error) {
    status(error.message, true);
  } finally {
    busy(false);
  }
});

for (const id of ["project", "scope"]) {
  byId(id).addEventListener("input", () => {
    if (!candidates.length) return;
    candidates = [];
    byId("top").replaceChildren(new Option("Scan the updated source directory"));
    byId("scan-summary").textContent = "";
    status("Scan sources to apply the new project settings.");
    busy(false);
  });
}

byId("compile").addEventListener("submit", async event => {
  event.preventDefault();
  const selected = candidates[byId("top").value];
  if (!selected) {
    status("Select a top module and source path.", true);
    return;
  }
  resetDisplay();
  busy(true);
  status("Compiling " + selected.name + "…");
  try {
    result = await request("compile", {...inputs(), top: selected.name, file: selected.file});
    byId("sources").replaceChildren(...result.files.map(path => make("li", "", path)));
    byId("file-count").textContent = result.files.length + " RTL files";
    byId("includes").append(make("p", "source", "Include directories · " + result.includeDirs.join(", ")));
    if (result.headers.length) {
      const headers = make("ul");
      for (const path of result.headers) headers.append(make("li", "source", path));
      byId("includes").append(make("h3", "", "Included headers"), headers);
    }
    byId("download").disabled = false;
    byId("diagnostics").textContent = result.diagnostics || "No compiler diagnostics.";
    byId("diagnostic-panel").open = !result.hierarchy;
    if (!result.hierarchy) {
      status("Compilation failed. See compiler diagnostics.", true);
      return;
    }
    status("Compiled and elaborated " + selected.name + ".");
    byId("instance-count").textContent = count(result.hierarchy) + " instances";
    focus([result.hierarchy]);
  } catch (error) {
    status(error.message, true);
  } finally {
    busy(false);
  }
});

byId("download").addEventListener("click", () => {
  if (!result) return;
  const url = URL.createObjectURL(new Blob([result.filelist], {type: "text/plain"}));
  const link = make("a");
  link.href = url;
  link.download = "sources.f";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function count(node) {
  return 1 + node.children.reduce((total, child) => total + count(child), 0);
}

function focus(path) {
  chain = path;
  for (const node of chain) expanded.add(node.path);
  drawTree();
  drawDiagram();
}

function drawTree() {
  function branch(node, path, level) {
    const group = make("div");
    const row = make("div", "tree-row");
    row.style.marginInlineStart = Math.min(level, 12) * 0.7 + "rem";
    const open = expanded.has(node.path);
    if (node.children.length) {
      const toggle = make("button", "tree-toggle", open ? "▾" : "▸");
      toggle.setAttribute("aria-label", (open ? "Collapse " : "Expand ") + node.name);
      toggle.setAttribute("aria-expanded", open);
      toggle.addEventListener("click", () => {
        if (open) expanded.delete(node.path);
        else expanded.add(node.path);
        drawTree();
        const replacement = [...byId("tree").querySelectorAll(".tree-toggle")]
          .find(item => item.dataset.path === node.path);
        replacement?.focus();
      });
      toggle.dataset.path = node.path;
      row.append(toggle);
    } else {
      row.append(make("span", "tree-spacer"));
    }
    const item = make("button", "tree-item", node.name);
    if (node.name !== node.module) item.append(make("span", "tree-module", node.module));
    if (chain.at(-1).path === node.path) item.setAttribute("aria-current", "true");
    item.addEventListener("click", () => focus(path));
    row.append(item);
    group.append(row);
    if (open) {
      for (const child of node.children) group.append(branch(child, [...path, child], level + 1));
    }
    return group;
  }
  byId("tree").replaceChildren(branch(chain[0], [chain[0]], 0));
}

function drawDiagram() {
  const node = chain.at(-1);
  byId("crumbs").replaceChildren();
  for (const [index, instance] of chain.entries()) {
    if (index) byId("crumbs").append(make("span", "", "/"));
    const crumb = make("button", "crumb", instance.name);
    crumb.addEventListener("click", () => focus(chain.slice(0, index + 1)));
    byId("crumbs").append(crumb);
  }
  const chip = make("div", "chip");
  const title = make("div", "chip-title");
  title.append(make("strong", "", node.module), make("span", "", node.children.length + " child instances"));
  const canvas = make("canvas");
  canvas.id = "net-lines";
  canvas.setAttribute("aria-hidden", "true");
  const grid = make("div", "blocks");
  const endpoints = new Map();
  const register = (signal, card, label) => {
    if (!signal) return;
    if (!endpoints.has(signal)) endpoints.set(signal, []);
    endpoints.get(signal).push({card, label});
  };

  for (const port of node.ports) {
    register(port.internal, title, "Module " + port.direction + " · " + port.name);
  }
  for (const child of node.children) {
    const card = make("button", "block");
    card.append(make("strong", "", child.name),
      make("span", "", child.module + (child.children.length ? " · " + child.children.length + " blocks" : "")));
    card.addEventListener("click", () => focus([...chain, child]));
    grid.append(card);
    for (const port of child.ports) {
      for (const net of port.refs) register(net, card, child.name + "." + port.name + " · " + port.direction);
    }
  }

  if (node.logic.length) {
    const logicCard = make("button", "block");
    logicCard.append(make("strong", "", "Local RTL"),
      make("span", "", node.logic.length + " assignments / processes"));
    logicCard.addEventListener("click", () => {
      const detail = byId("local-logic");
      detail.open = true;
      detail.scrollIntoView({block: "nearest"});
      detail.querySelector("summary").focus({preventScroll: true});
    });
    grid.append(logicCard);
    for (const net of new Set(node.logic.flatMap(item => item.refs))) {
      register(net, logicCard, "Referenced in local RTL");
    }
  }
  if (!grid.children.length) grid.append(make("p", "empty", "No child instances or local processes."));
  chip.append(canvas, title, grid);

  const bar = make("div", "signal-bar");
  const anchor = make("span", "", "Trace signal");
  anchor.id = "net-anchor";
  const picker = make("select");
  picker.setAttribute("aria-label", "Trace signal");
  picker.append(new Option("Select a signal", ""));
  const label = net => net.startsWith(node.path + ".") ? net.slice(node.path.length + 1) : net;
  for (const key of [...endpoints.keys()].sort((a, b) => label(a).localeCompare(label(b)))) {
    picker.append(new Option(label(key), key));
  }
  picker.disabled = !endpoints.size;
  bar.append(anchor, picker);
  chip.append(bar);
  byId("diagram").replaceChildren(chip);
  const connections = drawInspector(node);
  picker.addEventListener("change", () => updateWires());
  window.onresize = updateWires;

  function updateWires() {
    const hits = endpoints.get(picker.value) || [];
    const bounds = chip.getBoundingClientRect();
    const scale = devicePixelRatio || 1;
    canvas.width = bounds.width * scale;
    canvas.height = bounds.height * scale;
    const ctx = canvas.getContext("2d");
    ctx.scale(scale, scale);
    ctx.strokeStyle = "#a8e8c3";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    const a = anchor.getBoundingClientRect();
    const fromX = a.left + a.width / 2 - bounds.left;
    const fromY = a.top - bounds.top - 4;
    const touched = new Set(hits.map(hit => hit.card));
    for (const card of grid.querySelectorAll(".block")) card.dataset.hit = touched.has(card);
    for (const card of touched) {
      const b = card.getBoundingClientRect();
      const toY = b.bottom - bounds.top;
      ctx.beginPath();
      ctx.moveTo(fromX, fromY);
      ctx.lineTo(fromX, fromY - 9);
      ctx.lineTo(b.left + b.width / 2 - bounds.left, toY + 4);
      ctx.lineTo(b.left + b.width / 2 - bounds.left, toY);
      ctx.stroke();
    }
    connections.replaceChildren();
    if (hits.length) {
      connections.append(make("h3", "", "Connections · " + label(picker.value)));
      const list = make("ul");
      for (const hit of hits) list.append(make("li", "", hit.label));
      connections.append(list);
    }
  }
}

function drawInspector(node) {
  const inspector = byId("inspector");
  const sourceLabel = source => source.file + ":" + source.line;
  inspector.replaceChildren(make("div", "source", sourceLabel(node.source)));
  if (node.parameters.length) {
    inspector.append(make("p", "source", node.parameters.map(p => p.name + " = " + p.value).join(" · ")));
  }
  if (node.ports.length) {
    const wrap = make("div", "table-wrap");
    const table = make("table");
    const header = table.createTHead().insertRow();
    for (const title of ["Port", "Direction", "Bits", "Parent binding"]) {
      const cell = make("th", "", title);
      cell.scope = "col";
      header.append(cell);
    }
    const body = table.createTBody();
    for (const port of node.ports) {
      const row = body.insertRow();
      for (const value of [port.name, port.direction, port.bits || "—", port.binding || "—"]) {
        row.append(make("td", "", value));
      }
    }
    wrap.append(table);
    inspector.append(wrap);
  }
  const connections = make("div");
  inspector.append(connections);
  if (node.logic.length) {
    const detail = make("details", "detail");
    detail.id = "local-logic";
    detail.append(make("summary", "", "Local RTL · " + node.logic.length));
    for (const item of node.logic) {
      const code = make("details", "logic-item");
      code.append(make("summary", "", item.kind + " · " + sourceLabel(item.source)), make("pre", "", item.text));
      detail.append(code);
    }
    inspector.append(detail);
  }
  return connections;
}
