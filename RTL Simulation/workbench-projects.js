"use strict";

const localCompiler = document.body.hasAttribute("data-local-compiler");
const githubDialog = byId("github-dialog");
const topPicker = byId("top");
let project;
let githubConnection;
let busy = false;

function setBusy(value) {
  busy = value;
  for (const id of ["connect-github", "open-folder", "open-files", "open-example", "github-refresh", "github-import-button", "close-project"]) {
    byId(id).disabled = value;
  }
  topPicker.disabled = value || !project?.candidates.length;
  byId("compile-button").disabled = value || !topPicker.value;
  byId("native-load").querySelector("button").disabled = value;
  githubDialog.querySelector(".source-head button").disabled = value;
}

function projectSourceUrl({file, line}) {
  if (project?.kind !== "github" || !Object.hasOwn(project.sources, file)) return "";
  const url = project.repositoryUrl + "/blob/" + project.commit + "/" + encodedPath(file);
  return line ? url + "#L" + line : url;
}

function drawProjectFiles() {
  const paths = Object.keys(project.sources || {}).sort();
  byId("project-file-count").textContent = paths.length ? String(paths.length) : "";
  const list = byId("project-files");
  if (!paths.length) {
    list.replaceChildren(make("li", "empty", "Elaborate a top module to browse its source files."));
    return;
  }
  list.replaceChildren(...paths.map(path => {
    const item = make("li");
    const button = make("button", "project-file", path);
    button.type = "button";
    button.addEventListener("click", () => openSource({file: path, line: 1}, project.sources));
    item.append(button);
    return item;
  }));
}

function activate(next, preferred) {
  project = next;
  if (next.kind !== "github") githubConnection = undefined;
  resetDisplay();
  byId("active-project").hidden = false;
  byId("project-name").textContent = next.name;
  byId("project-meta").textContent = next.kind === "github" ?
    next.ref + " · " + next.commit.slice(0, 10) + " · Read-only import" :
    next.kind === "example" ? "Bundled example" : "Local project";
  const fromGitHub = next.kind === "github";
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
  status(next.candidates.length ? "Choose a top module and elaborate the design." : "No module declarations found in the loaded sources.", !next.candidates.length);
}

function setGithubStatus(message, error = false) {
  byId("github-status").textContent = message;
  byId("github-status").dataset.error = error;
}

async function importGitHub(settings, token, inDialog = true) {
  const report = inDialog ? setGithubStatus : status;
  const preferred = project?.kind === "github" && project.candidates[topPicker.value];
  const recompile = !inDialog && preferred && result?.hierarchy;
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
    if (recompile && topPicker.value) await compileSelection();
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

byId("close-project").addEventListener("click", () => {
  project = undefined;
  githubConnection = undefined;
  resetDisplay();
  topPicker.replaceChildren();
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
    if (localCompiler) {
      options = {project: ".", scope: ""};
      const scan = await nativeRequest("scan", options);
      candidates = scan.tops.filter(item => ["riscv_core", "tb_riscv_core", "nexys4_top"].includes(item.name));
    } else {
      const response = await fetch("/compiled-designs.json");
      if (!response.ok) throw new Error("The RISC-V example could not be loaded.");
      designs = await response.json();
      candidates = Object.keys(designs).map(name => ({name, file: ""}));
    }
    const selected = candidates.find(item => item.name === "riscv_core") || candidates[0];
    if (!selected) throw new Error("The RISC-V example is unavailable in this checkout.");
    activate({name: "RISC-V", kind: "example", designs, options, candidates}, selected);
    await compileSelection();
  } catch (error) {
    status(error.message, true);
  } finally {
    setBusy(false);
  }
});

function browserCompile(loaded, selected) {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/rtl-compile-worker.js", {type: "module"});
    const timeout = setTimeout(() => finish(new Error("Compilation timed out. Select a smaller source folder.")), 150000);
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
    worker.onerror = () => finish(new Error("The browser RTL compiler stopped unexpectedly."));
    worker.postMessage({sources: loaded.sources, candidates: loaded.candidates, selected});
  });
}

async function compileSelection() {
  const selected = project?.candidates[topPicker.value];
  if (!selected) return;
  resetDisplay();
  status("Compiling " + selected.name + "…");
  try {
    const compiled = project.designs?.[selected.name] || (project.options ?
      await nativeRequest("compile", {...project.options, top: selected.name, file: selected.file}) :
      await browserCompile(project, selected));
    if (project.kind === "native" || project.kind === "example") {
      project.sources = compiled.sourceText;
      drawProjectFiles();
    }
    showResult(compiled);
  } catch (error) {
    status("Could not elaborate " + selected.name + ". See compiler diagnostics.", true);
    byId("diagnostics").textContent = error.message;
    byId("diagnostic-panel").open = true;
  }
}

topPicker.addEventListener("change", () => { byId("compile-button").disabled = busy || !topPicker.value; });
byId("compile").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !topPicker.value) return;
  setBusy(true);
  try {
    await compileSelection();
  } finally {
    setBusy(false);
  }
});
