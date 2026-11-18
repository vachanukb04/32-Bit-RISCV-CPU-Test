"use strict";

const workflows = [
  {
    tab: "Clocks",
    name: "Clocks & Relationships",
    window: "Clock Builder · SDC + Diagram",
    summary: "Create clocks at their RTL source pins and describe their relationships in SDC.",
    headline: "Visual SDC clock creation at the right RTL point",
    description: "Clock constraints tie each clock name to a specific port or pin. Generated clocks carry a source relationship so timing can follow the design hierarchy.",
    points: [
      ["Clock definitions", "Use create_clock for primary clocks and create_generated_clock for derived clocks."],
      ["Clock groups", "Group clocks by timing relationship and declare asynchronous domains in SDC."],
      ["RTL diagram", "Follow the clock source through PLLs, clock muxes, and downstream modules."],
      ["CDC constraints", "Review clock-domain crossings before applying max-delay constraints."]
    ],
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
    headline: "Set the mode before timing a scenario",
    description: "Case analysis applies a constant to a port or pin. Timing analysis can then exclude paths that are inactive for that value.",
    points: [["Mode pins", "Check the design pin and polarity for each scenario."]],
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
    headline: "Scope false paths to the intended signals",
    description: "A false-path exception removes matching timing paths from analysis. Use precise endpoints and confirm the functional reason for the exception.",
    points: [["Path scope", "Use from, through, and to filters to limit the matching paths."]],
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
    headline: "Make the capture cycle explicit",
    description: "A multicycle constraint changes the launch-to-capture relationship. Check the setup and hold adjustments together against the intended protocol.",
    points: [["Paired checks", "Review the setup and hold edges together on the reported timing path."]],
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
    headline: "Keep the reason with the exception",
    description: "A waiver records why an exception is valid in a given scenario. Include the affected pins and enough evidence for a reviewer to verify the waiver.",
    points: [["Review evidence", "Keep the RTL reference and the matching timing report together."]],
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
    headline: "Review the logic that allows extra cycles",
    description: "A multicycle waiver should identify the enable or protocol that prevents an earlier capture and document the paired setup and hold constraints.",
    points: [["Capture conditions", "Verify that the enable and launch-to-capture timing agree."]],
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
    headline: "Follow the design through timing analysis",
    description: "Static timing analysis uses the gate-level netlist, timing libraries, and constraints to calculate arrivals, required times, and slack.",
    points: [["Reports", "Review both setup and hold, and check for unconstrained endpoints."]],
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
    headline: "Connect timing results back to the design",
    description: "Use hierarchy and source references to understand where a clock, path, or exception begins and ends.",
    points: [["Design inspection", "Open the workbench to follow the actual RTL connectivity."]],
    form: "Review scope",
    fields: [["Hierarchy", "Top and instances"], ["Clocks", "Primary and generated"], ["Exceptions", "Setup and hold"]],
    queue: ["Inspect module hierarchy", "Follow signal connections", "Open linked RTL"],
    diagram: ["RTL", "CONSTRAINTS", "REPORTS"]
  }
];

const gallery = document.getElementById("workflow-gallery");
const tabs = document.getElementById("workflow-tabs");
const pages = document.getElementById("workflow-pages");
const preview = document.getElementById("workflow-window");
const copy = document.getElementById("workflow-copy");
const count = document.getElementById("workflow-count");
const chooser = document.getElementById("choose-workflow");
let current = 0;

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

function exampleWindow(workflow, thumbnail = false) {
  const window = element("span", thumbnail ? "workflow-window thumbnail-window" : "workflow-window");
  if (thumbnail) window.setAttribute("aria-hidden", "true");
  else {
    window.setAttribute("role", "group");
    window.setAttribute("aria-label", workflow.window + " preview");
  }
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
  for (const step of workflow.diagram) rails.append(element("span"));
  diagram.append(rails);

  const content = element("span", "preview-workspace");
  content.append(fields, queue, diagram);
  window.append(content);
  return window;
}

function control(label, className, index) {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", () => select(index));
  return button;
}

function select(index, moveGallery = true) {
  current = (index + workflows.length) % workflows.length;
  const workflow = workflows[current];
  for (const group of [gallery, tabs, pages]) {
    [...group.children].forEach((button, i) => button.setAttribute("aria-pressed", i === current));
  }
  preview.replaceChildren(exampleWindow(workflow));
  const list = element("ul");
  for (const [label, line] of workflow.points) {
    const row = element("li");
    row.append(element("strong", "", label + ". "), line);
    list.append(row);
  }
  copy.replaceChildren(element("p", "workflow-name", workflow.name),
    element("h3", "", workflow.headline), element("p", "", workflow.description), list);
  count.textContent = (current + 1) + " / " + workflows.length;
  if (moveGallery) {
    const card = gallery.children[current];
    const left = card.offsetLeft;
    const right = left + card.offsetWidth;
    if (current === 0 || left < gallery.scrollLeft) {
      gallery.scrollLeft = current === 0 ? 0 : left;
    } else if (right > gallery.scrollLeft + gallery.clientWidth) {
      gallery.scrollLeft = right - gallery.clientWidth;
    }
  }
}

workflows.forEach((workflow, i) => {
  const card = control("", "workflow-card", i);
  const caption = element("span", "workflow-card-caption");
  caption.append(element("strong", "", workflow.name), element("span", "", workflow.summary));
  card.append(exampleWindow(workflow, true), caption);
  gallery.append(card);
  tabs.append(control(workflow.tab, "workflow-tab", i));
  const page = control("", "preview-page", i);
  page.setAttribute("aria-label", workflow.name + " preview");
  pages.append(page);
});
document.getElementById("previous-workflow").addEventListener("click", () => select(current - 1));
document.getElementById("next-workflow").addEventListener("click", () => select(current + 1));
chooser.addEventListener("click", () => gallery.children[current].focus());
chooser.hidden = false;
document.querySelector(".workflow-pagination").hidden = false;
select(0, false);
