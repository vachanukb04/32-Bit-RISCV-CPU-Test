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
  for (const id of ["instance-count", "crumbs", "inspector", "sources", "file-count", "includes", "issues"]) {
    byId(id).replaceChildren();
  }
  byId("diagnostics").textContent = "No compile has been run.";
  byId("diagnostics").hidden = false;
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
    showResult(result);
  } catch (error) {
    status(error.message, true);
  } finally {
    busy(false);
  }
});

function showResult(compiled) {
  result = compiled;
  byId("sources").replaceChildren(...result.files.map(path => make("li", "", path)));
  byId("file-count").textContent = result.files.length + " RTL files";
  byId("includes").append(make("p", "source", "Include directories · " + result.includeDirs.join(", ")));
  if (result.headers.length) {
    const headers = make("ul");
    for (const path of result.headers) headers.append(make("li", "source", path));
    byId("includes").append(make("h3", "", "Included headers"), headers);
  }
  byId("download").disabled = false;
  drawDiagnostics(result);
  if (!result.hierarchy) {
    status("Compilation failed. See compiler diagnostics.", true);
    return;
  }
  status("Compiled and elaborated " + result.top + " · " + result.files.length + " source files.");
  byId("instance-count").textContent = count(result.hierarchy) + " instances";
  focus([result.hierarchy]);
}

function sourceLabel(source) {
  return source.file + ":" + source.line + (source.column ? ":" + source.column : "");
}

function sourceLink(source) {
  const label = sourceLabel(source);
  if (!Object.hasOwn(result.sourceText, source.file)) return make("span", "source", label);
  const button = make("button", "source-link", label);
  button.type = "button";
  button.addEventListener("click", () => openSource(source));
  return button;
}

function drawDiagnostics(compiled) {
  const issues = byId("issues");
  for (const issue of compiled.issues) {
    const card = make("div", "issue");
    card.append(make("span", "severity " + issue.severity, issue.severity), make("span", "", issue.message));
    if (issue.source) card.append(sourceLink(issue.source));
    issues.append(card);
  }
  byId("diagnostics").textContent = compiled.diagnostics || "No compiler diagnostics.";
  byId("diagnostics").hidden = compiled.issues.length > 0;
  if (compiled.issues.length && compiled.diagnostics) {
    const raw = make("details", "raw-diagnostics");
    raw.append(make("summary", "", "Full compiler output"), make("pre", "", compiled.diagnostics));
    issues.append(raw);
  }
  byId("diagnostic-panel").open = !compiled.hierarchy || compiled.issues.length > 0;
}

function openSource(source) {
  const view = byId("source-view");
  const listing = byId("source-lines");
  byId("source-path").textContent = sourceLabel(source);
  const fragment = document.createDocumentFragment();
  const lines = result.sourceText[source.file].split(/\r?\n/);
  let target;
  for (const [index, text] of lines.entries()) {
    const line = make("div", "source-line");
    line.append(make("span", "line-number", String(index + 1)));
    const code = make("code");
    if (index + 1 === source.line) {
      line.classList.add("selected-line");
      line.setAttribute("aria-current", "location");
      target = line;
      const characters = Array.from(text);
      const column = Math.min(Math.max((source.column || 1) - 1, 0), characters.length);
      code.append(characters.slice(0, column).join(""), make("mark", "", characters[column] || " "),
        characters.slice(column + 1).join(""));
    } else {
      code.textContent = text;
    }
    line.append(code);
    fragment.append(line);
  }
  listing.replaceChildren(fragment);
  if (!view.open) view.showModal();
  target?.scrollIntoView({block: "center", inline: "nearest"});
}

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
  const grid = make("div", "blocks");
  const endpoints = new Map();
  const register = (signal, card, label, role) => {
    if (!signal) return;
    if (!endpoints.has(signal)) endpoints.set(signal, []);
    endpoints.get(signal).push({card, label, role});
  };

  for (const port of node.ports) {
    const role = port.direction === "input" ? "driver" : port.direction === "output" ? "load" : "shared";
    register(port.internal, title, "Module " + port.direction + " · " + port.name, role);
  }
  for (const child of node.children) {
    const card = make("button", "block");
    card.append(make("strong", "", child.name),
      make("span", "", child.module + (child.children.length ? " · " + child.children.length + " blocks" : "")));
    card.addEventListener("click", () => focus([...chain, child]));
    grid.append(card);
    for (const port of child.ports) {
      for (const net of port.refs) {
        const role = port.direction === "inout" ? "shared" : port.drives.includes(net) ? "driver" : "load";
        register(net, card, child.name + "." + port.name + " · " + port.direction, role);
      }
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
    for (const item of node.logic) {
      const label = item.kind + " · line " + item.source.line;
      for (const net of item.outputs) register(net, logicCard, label + " writes", "driver");
      for (const net of item.inputs) register(net, logicCard, label + " reads", "load");
    }
  }

  const bar = make("div", "signal-bar");
  const pickerLabel = make("label", "", "Trace signal");
  pickerLabel.htmlFor = "signal-picker";
  const picker = make("select");
  picker.id = "signal-picker";
  picker.append(new Option("Select a signal", ""));
  const label = net => net.startsWith(node.path + ".") ? net.slice(node.path.length + 1) : net;
  for (const key of [...endpoints.keys()].sort((a, b) => label(a).localeCompare(label(b)))) {
    picker.append(new Option(label(key), key));
  }
  picker.disabled = !endpoints.size;
  bar.append(pickerLabel, picker);
  const trace = make("div", "trace-flow");
  trace.hidden = true;
  chip.append(title, bar, trace);
  if (grid.children.length) chip.append(grid);
  if (node.structures.length) chip.append(drawStructures(node.structures, label));
  if (!grid.children.length && !node.structures.length) {
    chip.append(make("p", "empty", "No child instances or local processes."));
  }
  byId("diagram").replaceChildren(chip);
  drawInspector(node);

  picker.addEventListener("change", () => {
    const hits = endpoints.get(picker.value) || [];
    const touched = new Set(hits.map(hit => hit.card));
    for (const card of grid.querySelectorAll(".block")) card.dataset.hit = touched.has(card);
    trace.hidden = !hits.length;
    trace.replaceChildren();
    if (!hits.length) return;

    function lane(role, heading, fallback) {
      const section = make("div", "trace-lane");
      section.append(make("h3", "", heading));
      const labels = new Set(hits.filter(hit => hit.role === role).map(hit => hit.label));
      for (const text of labels) section.append(make("div", "trace-terminal", text));
      if (!labels.size) section.append(make("p", "source", fallback));
      return section;
    }

    const net = make("div", "trace-net");
    net.append(make("span", "", "Selected signal"), make("strong", "", label(picker.value)));
    trace.append(lane("driver", "Drivers", "No driver shown in this scope"), net,
      lane("load", "Loads", "No load shown in this scope"));
    if (hits.some(hit => hit.role === "shared")) {
      trace.append(lane("shared", "Bidirectional / interface", ""));
    }
  });
}

function drawStructures(structures, signalLabel) {
  const section = make("section", "structures");
  section.append(make("h3", "structures-title", "RTL structures"));
  const kinds = [
    ["flop", "Flip-flops"], ["latch", "Latches"], ["memory", "Memories"],
    ["mux", "Multiplexers"], ["gate", "Combinational gates"], ["operator", "Datapath operators"],
  ];
  for (const [kind, name] of kinds) {
    const items = structures.filter(item => item.kind === kind);
    if (!items.length) continue;
    section.append(make("h4", "", name));
    const row = make("div", "structure-grid");
    for (const item of items) {
      const card = make("details", "structure-card");
      const heading = make("summary");
      heading.append(make("strong", "", item.label));
      const summary = [item.bits ? item.bits + " bit" + (item.bits === 1 ? "" : "s") : "",
        item.occurrences.length > 1 ? item.occurrences.length + " occurrences" : ""].filter(Boolean).join(" · ");
      if (summary) heading.append(make("span", "source", summary));
      if (item.detail) heading.append(make("span", "source", item.detail));
      card.append(heading);
      const info = make("div", "structure-info");
      if (item.inputs.length) info.append(make("p", "source", "References · " + item.inputs.map(signalLabel).join(", ")));
      if (item.outputs.length) info.append(make("p", "source", "Assignment context · " + item.outputs.map(signalLabel).join(", ")));
      for (const entry of item.occurrences) {
        info.append(sourceLink(entry.source));
        if (entry.text) info.append(make("pre", "", entry.text));
      }
      card.append(info);
      row.append(card);
    }
    section.append(row);
  }
  return section;
}

function drawInspector(node) {
  const inspector = byId("inspector");
  inspector.replaceChildren(sourceLink(node.source));
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
}
