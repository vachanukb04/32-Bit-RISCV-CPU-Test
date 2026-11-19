"use strict";

const localCompiler = document.body.hasAttribute("data-local-compiler");
const githubDialog = byId("github-dialog");
const createDialog = byId("create-dialog");
const topPicker = byId("top");
const simulateLink = byId("simulate-workflow");
const simulationWorkspace = byId("simulation-workspace");
const reviewButton = byId("review-proceed");
let project;
let githubConnection;
let busy = false;
let simulationApproved = false;

function updateReview() {
  const selected = project?.candidates[topPicker.value];
  const hasSources = Object.keys(project?.sources || {}).some(file => HDL_SOURCE.test(file));
  reviewButton.disabled = busy || !selected || !hasSources;
  byId("review-status").textContent = !project ? "Load a project to review its RTL." :
    !project.candidates.length ? "Add RTL files to your project." :
    !selected ? "Choose the top module you want to review." :
    !hasSources ? "Analyze the selected top to load its source files." :
    simulationApproved ? "This RTL is ready for simulation." : "Review the RTL before continuing to simulation.";
}

function showWorkflow() {
  const simulating = simulationApproved && location.hash === "#simulate";
  document.body.classList.toggle("simulating", simulating);
  byId("design-workspace").hidden = simulating;
  simulationWorkspace.hidden = !simulating;
  const understandLink = byId("understand-workflow");
  if (simulating) understandLink.removeAttribute("aria-current");
  else understandLink.setAttribute("aria-current", "page");
  if (simulating) simulateLink.setAttribute("aria-current", "page");
  else simulateLink.removeAttribute("aria-current");
  document.title = (simulating ? "Simulate your design" : "Understanding your design") + " · RTL2GDS Cockpit";
}

function invalidateSimulation() {
  if (!simulationApproved) return;
  simulationApproved = false;
  simulationWorkspace.replaceChildren();
  simulateLink.removeAttribute("href");
  simulateLink.setAttribute("aria-disabled", "true");
  simulateLink.title = "Review your RTL to unlock simulation";
  if (location.hash === "#simulate") history.replaceState(null, "", "#design");
  showWorkflow();
}

reviewButton.addEventListener("click", () => {
  if (reviewButton.disabled) return;
  if (!simulationApproved) {
    const snapshot = reviewedSimulation(project, project.candidates[topPicker.value], result);
    if (!Object.keys(snapshot.sources).some(file => HDL_SOURCE.test(file))) {
      byId("review-status").textContent = "Analyze the selected top to load its source files.";
      return;
    }
    const frame = document.createElement("iframe");
    frame.title = "Simulate your design";
    frame.addEventListener("load", () => {
      frame.contentWindow.postMessage({type: "reviewed-rtl", project: snapshot}, location.origin);
    }, {once: true});
    frame.src = "/simulate.html";
    simulationWorkspace.replaceChildren(frame);
    simulationApproved = true;
    simulateLink.href = "#simulate";
    simulateLink.removeAttribute("aria-disabled");
    simulateLink.removeAttribute("title");
  }
  updateReview();
  location.hash = "simulate";
  showWorkflow();
  simulateLink.focus({preventScroll: true});
});

window.addEventListener("hashchange", showWorkflow);

function setBusy(value) {
  busy = value;
  for (const id of ["connect-github", "create-project", "open-folder", "open-files", "open-example", "github-refresh", "github-import-button", "close-project"]) {
    byId(id).disabled = value;
  }
  topPicker.disabled = value || !project?.candidates.length;
  byId("compile-button").disabled = value || !topPicker.value;
  byId("native-load").querySelector("button").disabled = value;
  githubDialog.querySelector(".source-head button").disabled = value;
  updateReview();
}

function projectSourceUrl({file, line}) {
  if (project?.kind !== "github" || !Object.hasOwn(project.sources, file)) return "";
  const url = project.repositoryUrl + "/blob/" + project.commit + "/" + encodedPath(file);
  return line ? url + "#L" + line : url;
}

function drawProjectFiles(sources = project.sources) {
  const paths = Object.keys(sources || {}).sort();
  byId("project-file-count").textContent = paths.length ? String(paths.length) : "";
  const list = byId("project-files");
  if (!paths.length) {
    list.replaceChildren(make("li", "empty", project.kind === "created" ?
      "Open RTL files or a folder to add sources." : "Analyze a top module to browse its source files."));
    return;
  }
  list.replaceChildren(...paths.map(path => {
    const item = make("li");
    const button = make("button", "project-file", path);
    button.type = "button";
    button.addEventListener("click", () => openSource({file: path, line: 1}, sources));
    item.append(button);
    return item;
  }));
}

function activate(next, preferred) {
  invalidateSimulation();
  project = next;
  if (next.kind !== "github") githubConnection = undefined;
  resetDisplay();
  byId("active-project").hidden = false;
  byId("project-name").textContent = next.name;
  byId("project-meta").textContent = next.kind === "github" ?
    (next.ref === "HEAD" ? "Default branch" : next.ref) + " · " + next.commit.slice(0, 10) + " · Read-only import" :
    next.kind === "example" ? "RISC-V example" : "Local project";
  const fromGitHub = next.kind === "github";
  byId("open-files").textContent = next.kind === "created" ? "Add RTL files" : "Open RTL files";
  byId("open-folder").textContent = next.kind === "created" ? "Add RTL folder" : "Open RTL folder";
  byId("github-actions").hidden = !fromGitHub;
  if (fromGitHub) byId("github-source").href = next.repositoryUrl + "/tree/" + next.commit;
  else byId("github-source").removeAttribute("href");

  const mock = new Option("Choose a top module", "");
  mock.disabled = true;
  topPicker.replaceChildren(mock, ...next.candidates.map(({name, file}, index) =>
    new Option(file ? name + " — " + file : name, String(index))));
  const matched = preferred ? next.candidates.findIndex(item => item.name === preferred.name && item.file === preferred.file) : -1;
  topPicker.value = matched >= 0 ? String(matched) :
    (next.candidates.length === 1 ? "0" : "");
  drawProjectFiles();
  if (next.kind === "created" && !next.candidates.length) {
    status("Project created. Add RTL files or a folder to get started.");
  } else {
    status(next.candidates.length ? "Choose a top module and analyze the RTL." : "No module declarations found in the loaded sources.", !next.candidates.length);
  }
}

function setGithubStatus(message, error = false) {
  byId("github-status").textContent = message;
  byId("github-status").dataset.error = error;
}

async function importGitHub(settings, token, inDialog = true) {
  const report = inDialog ? setGithubStatus : status;
  const preferred = project?.kind === "github" && project.candidates[topPicker.value];
  const analyzeAgain = !inDialog && preferred && result?.hierarchy;
  let imported = false;
  setBusy(true);
  report("Reading GitHub repository…");
  try {
    const loaded = await loadGitHub(settings, token, (count, total) => report("Reading RTL files · " + count + " / " + total));
    loaded.candidates = indexRtl(loaded.sources);
    if (!loaded.candidates.length) throw new Error("The selected GitHub sources have no module declarations.");
    activate(loaded, preferred || undefined);
    githubConnection = {settings: {...settings, ref: loaded.ref}, token};
    imported = true;
    if (inDialog) githubDialog.close();
    if (analyzeAgain && topPicker.value) await analyzeSelection();
  } catch (error) {
    report(error.message, true);
  } finally {
    setBusy(false);
    if (imported && inDialog) topPicker.focus();
  }
}

byId("connect-github").addEventListener("click", () => {
  setGithubStatus("");
  githubDialog.showModal();
});
githubDialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
githubDialog.addEventListener("close", () => { byId("github-token").value = ""; });
byId("github-import").addEventListener("submit", event => {
  event.preventDefault();
  if (busy) return;
  try {
    const settings = parseRepository(byId("github-repo").value.trim(), byId("github-ref").value.trim(), byId("github-scope").value.trim());
    const token = byId("github-token").value.trim();
    byId("github-token").value = "";
    importGitHub(settings, token);
  } catch (error) {
    setGithubStatus(error.message, true);
  }
});
byId("github-refresh").addEventListener("click", () => {
  if (!githubConnection || busy) return;
  const {settings, token} = githubConnection;
  importGitHub(settings, token, false);
});

byId("create-project").addEventListener("click", () => {
  byId("project-create-form").reset();
  createDialog.showModal();
});
byId("project-create-form").addEventListener("submit", event => {
  event.preventDefault();
  const name = byId("new-project-name").value.trim();
  if (!name) return;
  activate({kind: "created", name, sources: {}, candidates: []});
  createDialog.close();
  setBusy(false);
  byId("open-files").focus();
});

byId("close-project").addEventListener("click", () => {
  invalidateSimulation();
  project = undefined;
  githubConnection = undefined;
  resetDisplay();
  topPicker.replaceChildren();
  byId("open-files").textContent = "Open RTL files";
  byId("open-folder").textContent = "Open RTL folder";
  byId("active-project").hidden = true;
  byId("github-source").removeAttribute("href");
  byId("project-file-count").textContent = "";
  byId("project-files").replaceChildren(make("li", "empty", "Load a project to browse its sources."));
  status("Choose a project source to get started.");
  setBusy(false);
  byId("connect-github").focus();
});

for (const [trigger, input, folder] of [["open-folder", "folder-input", true], ["open-files", "files-input", false]]) {
  byId(trigger).addEventListener("click", () => byId(input).click());
  byId(input).addEventListener("change", async event => {
    if (!event.target.files.length || busy) return;
    setBusy(true);
    status("Reading local RTL…");
    try {
      const loaded = await loadSelectedFiles(event.target.files, folder);
      if (project?.kind === "created") {
        loaded.kind = "created";
        loaded.name = project.name;
        loaded.sources = {...project.sources, ...loaded.sources};
      }
      loaded.candidates = indexRtl(loaded.sources);
      if (!loaded.candidates.length) throw new Error("The selected files have no module declarations.");
      activate(loaded);
    } catch (error) {
      status(error.message, true);
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  });
}

async function nativeRequest(kind, options) {
  const response = await fetch("/api/" + kind, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(options)});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

if (localCompiler) {
  byId("native-project").hidden = false;
  byId("native-load").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    status("Scanning the local project…");
    const options = {project: byId("native-path").value, scope: byId("native-scope").value};
    try {
      const data = await nativeRequest("scan", options);
      activate({name: options.project || "Local RTL", kind: "native", candidates: data.tops, options});
    } catch (error) {
      status(error.message, true);
    } finally {
      setBusy(false);
    }
  });
}

byId("open-example").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  status("Opening the RISC-V example…");
  try {
    let designs;
    let options;
    let candidates;
    let sources;
    if (localCompiler) {
      options = {project: ".", scope: "RiscV-32bit/RiscV-32bit.srcs/sources_1/new"};
      const scan = await nativeRequest("scan", options);
      candidates = scan.tops;
    } else {
      const response = await fetch("/compiled-designs.json");
      if (!response.ok) throw new Error("The RISC-V example could not be loaded.");
      designs = await response.json();
      sources = Object.assign({}, ...Object.values(designs).map(design => design.sourceText));
      candidates = indexRtl(sources);
    }
    const selected = candidates.find(item => item.name === "riscv_core") || candidates[0];
    if (!selected) throw new Error("The RISC-V example is unavailable in this checkout.");
    activate({name: "RISC-V", kind: "example", designs, options, candidates, sources}, selected);
    await analyzeSelection();
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
});

function browserAnalyze(loaded, selected) {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/rtl-analyze-worker.js", {type: "module"});
    const timeout = setTimeout(() => finish(new Error("RTL analysis timed out. Select a smaller source folder.")), 150000);
    function finish(error, result) {
      clearTimeout(timeout);
      worker.terminate();
      if (error) reject(error);
      else resolve(result);
    }
    worker.onmessage = ({data}) => {
      if (data.stage) status(data.stage);
      else if (data.error) finish(new Error(data.error));
      else finish(null, data.result);
    };
    worker.onerror = () => finish(new Error("The browser RTL analyzer stopped unexpectedly."));
    worker.postMessage({sources: loaded.sources, selected});
  });
}

async function analyzeSelection() {
  const selected = project?.candidates[topPicker.value];
  if (!selected) return;
  resetDisplay();
  if (project.kind === "example") byId("project-name").textContent = project.name + " · " + selected.name;
  status("Analyzing " + selected.name + "…");
  try {
    const cached = project.designs?.[selected.name];
    const compiled = cached?.hierarchy?.source.file === selected.file ? cached : project.options ?
      await nativeRequest("compile", {...project.options, top: selected.name, file: selected.file}) :
      await browserAnalyze(project, selected);
    if (project.options) project.sources = compiled.sourceText;
    if (project.options || project.kind === "example") drawProjectFiles(compiled.sourceText);
    showResult(compiled);
  } catch (error) {
    status("Could not analyze " + selected.name + ". See compiler diagnostics.", true);
    byId("diagnostics").textContent = error.message;
    byId("diagnostic-panel").open = true;
  }
}

async function analyzeTop() {
  if (busy || !topPicker.value) return;
  setBusy(true);
  try {
    await analyzeSelection();
  } finally {
    setBusy(false);
  }
}

topPicker.addEventListener("change", () => {
  invalidateSimulation();
  if (project?.options) {
    project.sources = undefined;
    drawProjectFiles();
  }
  resetDisplay();
  updateReview();
  byId("compile-button").disabled = busy || !topPicker.value;
  if (project?.kind === "example") analyzeTop();
  else if (topPicker.value) status("Analyze the selected top to view its logical block diagram.");
});
byId("compile").addEventListener("submit", event => {
  event.preventDefault();
  analyzeTop();
});
