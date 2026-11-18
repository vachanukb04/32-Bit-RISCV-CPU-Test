"use strict";

const pausedWorkflows = [
  {
    tab: "Clocks",
    name: "Clocks & Relationships",
    window: "Clock Builder · SDC + Diagram",
    summary: "Create clocks at their RTL source pins and describe their relationships in SDC.",
    modes: ["FUNC", "ATPG", "ScanShift", "ScanCap"],
    form: "Create clock",
    fields: [["Name", "core_clk"], ["Source", "TOP.clk"], ["Period", "4.0 ns"]],
    queue: ["core_clk · create", "cdb_clk_by2 · generate", "ddr_aclk · create"],
    diagram: ["PLL", "CMC", "DMC"]
  },
  {
    tab: "Case",
    name: "Case Analysis",
    window: "Case Analysis Generator",
    summary: "Tie mode pins to known values for functional, scan, and test scenarios.",
    modes: ["FUNC", "ATPG", "ScanShift", "ScanCap"],
    form: "Set case analysis",
    fields: [["scan_en", "0"], ["test_mode", "0"], ["clk_bypass", "0"]],
    queue: ["set_case_analysis 0 [get_ports scan_en]", "set_case_analysis 0 [get_ports test_mode]"],
    diagram: ["MODE", "MUX", "FUNC"]
  },
  {
    tab: "False Path",
    name: "False Paths",
    window: "False Path Constraints",
    summary: "Define exceptions by startpoint, through points, and endpoint.",
    modes: ["FUNC", "ScanShift"],
    form: "Set false path",
    fields: [["From", "test_clk"], ["Through", "u_test/mux"], ["To", "core_clk"]],
    queue: ["set_false_path -from test_clk -to core_clk", "Review matched endpoints"],
    diagram: ["START", "THRU", "END"]
  },
  {
    tab: "MCP",
    name: "Multi-Cycle Paths",
    window: "Multi-Cycle Path Constraints",
    summary: "Pair setup and hold exceptions for paths with a longer capture interval.",
    modes: ["FUNC", "ScanShift"],
    form: "Set multicycle path",
    fields: [["From", "u_src/Q"], ["To", "u_dst/D"], ["Setup / hold", "2 / 1"]],
    queue: ["set_multicycle_path 2 -setup", "set_multicycle_path 1 -hold"],
    diagram: ["LAUNCH", "CYCLE 1", "CAPTURE"]
  },
  {
    tab: "FP Waiver",
    name: "False Path Waivers",
    window: "False Path Waiver Review",
    summary: "Review the endpoints, mode, and purpose of a false-path exception.",
    form: "Review false path",
    fields: [["Mode", "FUNC"], ["Scope", "test → core"], ["Reason", "Inactive test path"]],
    queue: ["Check mode bindings", "Review matched paths", "Record rationale"],
    diagram: ["EXCEPTION", "EVIDENCE", "REVIEW"]
  },
  {
    tab: "MCP Waiver",
    name: "Multi-Cycle Path Waivers",
    window: "Multi-Cycle Waiver Review",
    summary: "Document the cycle count and capture conditions supporting an exception.",
    form: "Review cycle allowance",
    fields: [["Mode", "FUNC"], ["Capture", "Every second cycle"], ["Setup / hold", "2 / 1"]],
    queue: ["Inspect capture enable", "Compare setup and hold", "Record rationale"],
    diagram: ["ENABLE", "TIMING", "REVIEW"]
  },
  {
    tab: "STA Flow",
    name: "Static Timing Analysis",
    window: "STA Flow Overview",
    summary: "See how libraries, netlists, and constraints feed timing reports.",
    form: "Analysis inputs",
    fields: [["Netlist", "design.v"], ["Constraints", "design.sdc"], ["Library", "typical.lib"]],
    queue: ["Read design and timing libraries", "Apply SDC", "Update and report timing"],
    diagram: ["READ", "ANALYZE", "REPORT"]
  },
  {
    tab: "Dashboard",
    name: "Design Dashboard",
    window: "Design Review Dashboard",
    summary: "Bring hierarchy, clocks, exceptions, and timing reports into one review.",
    form: "Review scope",
    fields: [["Hierarchy", "Top and instances"], ["Clocks", "Primary and generated"], ["Exceptions", "Setup and hold"]],
    queue: ["Inspect module hierarchy", "Follow signal connections", "Open linked RTL"],
    diagram: ["RTL", "CONSTRAINTS", "REPORTS"]
  }
];

const gallery = document.getElementById("workflow-gallery");
const tabs = document.getElementById("workflow-tabs");
function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value) node.textContent = value;
  return node;
}

function previewPanel(title) {
  const panel = element("span", "preview-panel");
  panel.append(element("strong", "preview-panel-title", title));
  return panel;
}

function pausedPreview(workflow) {
  const window = element("span", "workflow-window thumbnail-window");
  window.setAttribute("aria-hidden", "true");
  const heading = element("span", "preview-heading");
  const lights = element("span", "window-dots");
  lights.setAttribute("aria-hidden", "true");
  lights.append(element("i"), element("i"), element("i"));
  heading.append(lights, element("strong", "", workflow.window));
  window.append(heading);

  const modes = element("span", "preview-modes");
  for (const name of workflow.modes || ["Overview"]) modes.append(element("span", "", name));
  window.append(modes);

  const fields = previewPanel(workflow.form);
  for (const [label, value] of workflow.fields) {
    const row = element("span", "preview-field");
    row.append(element("span", "", label), element("code", "", value));
    fields.append(row);
  }
  const queue = previewPanel("Staged queue");
  const entries = element("span", "preview-queue");
  for (const line of workflow.queue) entries.append(element("code", "", line));
  queue.append(entries);

  const diagram = previewPanel("RTL diagram");
  const steps = element("span", "preview-steps");
  for (const step of workflow.diagram) steps.append(element("code", "", step));
  diagram.append(steps);
  const rails = element("span", "preview-rails");
  rails.setAttribute("aria-hidden", "true");
  rails.append(...workflow.diagram.map(() => element("span")));
  diagram.append(rails);

  const content = element("span", "preview-workspace");
  content.append(fields, queue, diagram);
  window.append(content);
  return window;
}

function pausedButton(className, label) {
  const button = element("button", className, label);
  button.type = "button";
  button.disabled = true;
  return button;
}

const designThumbnail = document.getElementById("design-preview").cloneNode(true);
designThumbnail.removeAttribute("id");
designThumbnail.removeAttribute("role");
designThumbnail.removeAttribute("aria-label");
designThumbnail.classList.add("thumbnail-window");
designThumbnail.setAttribute("aria-hidden", "true");
designThumbnail.querySelector(".preview-heading strong").textContent = "RTL Workbench · Hierarchy + Schematic";
document.getElementById("design-workflow").prepend(designThumbnail);

for (const workflow of pausedWorkflows) {
  const card = pausedButton("workflow-card");
  const caption = element("span", "workflow-card-caption");
  caption.append(element("strong", "", workflow.name), element("span", "", workflow.summary));
  card.append(pausedPreview(workflow), caption);
  gallery.append(card);
  tabs.append(pausedButton("workflow-tab", workflow.tab));
}
