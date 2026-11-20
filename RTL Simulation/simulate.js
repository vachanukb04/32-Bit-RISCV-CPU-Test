"use strict";

const el = id => document.getElementById(id);
const consoleOutput = el("sim-console");
const canvas = el("sim-canvas");
const timeCursor = el("sim-time");
let localSources = {};
let reviewed = false;
let waveFile;
let waveform;
const selectedSignals = new Set();
const phaseNames = ["Analyze", "Elaborate", "Simulate"];
let currentPhase = -1;

function simStatus(message, error = false) {
  el("sim-status").textContent = message;
  el("sim-status").dataset.error = error;
}

function showPhase(index, state) {
  currentPhase = index;
  for (const [position, name] of phaseNames.entries()) {
    const item = el("sim-" + name.toLowerCase());
    const phase = position < index ? "complete" : position === index ? state : "waiting";
    item.dataset.state = phase;
    item.querySelector("span").textContent = phase[0].toUpperCase() + phase.slice(1);
  }
}

function listSimulationFiles() {
  const paths = Object.keys(localSources);
  el("sim-input-summary").textContent = paths.length + (paths.length === 1 ? " source file" : " source files");
  el("sim-input-list").replaceChildren(...paths.map(path => {
    const item = document.createElement("li");
    item.textContent = path;
    return item;
  }));
}

window.addEventListener("message", event => {
  if (reviewed || window.parent === window || event.source !== window.parent ||
      event.origin !== location.origin || event.data?.type !== "reviewed-rtl") return;
  const project = event.data.project;
  if (!Object.keys(project?.sources || {}).some(file => HDL_SOURCE.test(file))) return;
  reviewed = true;
  localSources = project.sources;
  el("sim-top").value = project.testbench;
  el("sim-project").textContent = "RTL2GDS Simulator · " + project.name + " · Reviewed top " + project.top;
  listSimulationFiles();
  el("sim-masthead").hidden = true;
  el("sim-locked").hidden = true;
  el("sim-active").hidden = false;
  simStatus(project.testbench ? "Project RTL loaded. Ready to run " + project.testbench + "." :
    "Project RTL loaded. Choose a testbench top or add a testbench.");
});

el("sim-open").onclick = () => el("sim-files").click();
el("sim-files").onchange = async event => {
  if (!reviewed || !event.target.files.length) return;
  try {
    const loaded = await loadSelectedFiles(event.target.files);
    const entries = Object.entries(loaded.sources);
    if (entries.some(([path, text]) => Object.hasOwn(localSources, path) && localSources[path] !== text)) {
      throw new Error("A selected file shares a path with a loaded source. Rename the new file or update it in the design workbench.");
    }
    localSources = {...localSources, ...loaded.sources};
    listSimulationFiles();
    if (!el("sim-top").value) {
      const modules = indexRtl(localSources);
      el("sim-top").value = modules.find(item => /^tb_|_tb$|testbench/i.test(item.name))?.name ||
        (modules.length === 1 ? modules[0].name : "");
    }
    simStatus("Loaded " + entries.length + (entries.length === 1 ? " source file." : " source files."));
  } catch (error) {
    simStatus(error.message, true);
  } finally {
    event.target.value = "";
  }
};

function runSimulation(sources, top, settings) {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/rtl-sim-worker.js", {type: "module"});
    const deadline = setTimeout(() => complete(new Error("Simulation timed out. Check that the testbench reaches $finish.")), 60000);
    function complete(error, result) {
      clearTimeout(deadline);
      worker.terminate();
      if (error) reject(error);
      else resolve(result);
    }
    worker.onmessage = ({data}) => {
      if (data.loading) {
        simStatus("Loading RTL2GDS Simulator…");
      } else if (data.stage) {
        showPhase(phaseNames.indexOf(data.stage), "running");
        simStatus(data.stage + " · " + top);
      } else if (data.error) {
        if (data.log) consoleOutput.textContent = data.log;
        const phase = phaseNames.indexOf(data.phase);
        if (phase >= 0) showPhase(phase, "failed");
        complete(new Error((data.phase ? data.phase + " · " : "") + data.error));
      } else {
        complete(null, data.result);
      }
    };
    worker.onerror = () => complete(new Error("The browser simulator stopped unexpectedly."));
    worker.postMessage({sources, top, settings});
  });
}

el("simulation-form").onsubmit = async event => {
  event.preventDefault();
  if (!reviewed) return;
  const sources = {...localSources};
  const input = el("sim-testbench").value;
  let filename = "__cockpit_testbench.sv";
  while (Object.hasOwn(sources, filename)) filename = "_" + filename;
  if (input.trim()) sources[filename] = input;
  const top = el("sim-top").value.trim();
  const settingsList = id => el(id).value.split(",").map(value => value.trim()).filter(Boolean);
  const settings = {includes: settingsList("sim-include"), defines: settingsList("sim-defines"),
    trace: settingsList("sim-trace"), limit: el("sim-limit").value === "" ? null : el("sim-limit").valueAsNumber};
  const controls = ["sim-run", "sim-open", "sim-top", "sim-testbench", "sim-include", "sim-defines", "sim-trace", "sim-limit"]
    .map(id => el(id));
  for (const control of controls) control.disabled = true;
  consoleOutput.textContent = "Running simulation…";
  showPhase(-1, "waiting");
  displayWaves(null);
  try {
    const result = await runSimulation(sources, top, settings);
    consoleOutput.textContent = result.log || "The testbench produced no console output.";
    displayWaves(result.wave);
    const outcome = result.testErrors ? "Testbench reported " + result.testErrors + " error(s). " : "";
    showPhase(result.testErrors ? 2 : 3, result.testErrors ? "failed" : "complete");
    simStatus(outcome + result.engine + " · " + result.reason + " · " + result.ticks + " × " + result.timescale + ".",
      Boolean(result.testErrors));
  } catch (error) {
    if (currentPhase < 0) showPhase(0, "failed");
    else showPhase(currentPhase, "failed");
    if (consoleOutput.textContent === "Running simulation…") consoleOutput.textContent = error.message;
    simStatus(error.message, true);
  } finally {
    for (const control of controls) control.disabled = false;
  }
};

function displayWaves(file) {
  waveFile = file;
  waveform = file ? readVcd(file.text) : null;
  selectedSignals.clear();
  const signals = waveform?.signals || [];
  el("sim-download").disabled = !file;
  el("sim-wave-view").hidden = !signals.length;
  el("sim-wave-empty").hidden = !!signals.length;
  const picker = el("sim-wave-picker");
  picker.replaceChildren(...signals.map((signal, index) => {
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = index < 6;
    if (checkbox.checked) selectedSignals.add(signal.id);
    checkbox.onchange = () => {
      if (checkbox.checked && selectedSignals.size >= 12) {
        checkbox.checked = false;
        simStatus("Display up to 12 signals at a time.");
        return;
      }
      if (checkbox.checked) selectedSignals.add(signal.id);
      else selectedSignals.delete(signal.id);
      drawWaves();
    };
    label.append(checkbox, signal.name);
    return label;
  }));
  timeCursor.max = waveform?.time || 0;
  timeCursor.value = 0;
  if (signals.length) drawWaves();
}

function drawWaves() {
  if (!waveform?.signals.length) return;
  const signals = waveform.signals.filter(signal => selectedSignals.has(signal.id));
  const width = Math.max(300, canvas.clientWidth);
  const ratio = Math.min(devicePixelRatio || 1, 2);
  const height = Math.max(85, 42 + 37 * signals.length);
  canvas.style.height = height + "px";
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.fillStyle = "#111a27";
  ctx.fillRect(0, 0, width, height);
  const left = Math.min(163, width * .38);
  const right = width - 13;
  const max = Math.max(1, waveform.time);
  const x = time => left + (right - left) * time / max;
  ctx.font = "11px monospace";
  for (let index = 0; index <= 5; index++) {
    const time = max * index / 5;
    const at = x(time);
    ctx.strokeStyle = "#34465f";
    ctx.beginPath();
    ctx.moveTo(at, 21);
    ctx.lineTo(at, height - 9);
    ctx.stroke();
    ctx.fillStyle = "#9cafc7";
    ctx.textAlign = index === 5 ? "right" : "left";
    ctx.fillText(String(Math.round(time)), at + (index === 5 ? -3 : 3), 15);
  }
  ctx.textAlign = "left";
  ctx.fillText(waveform.unit, 12, 15);

  for (const [index, signal] of signals.entries()) {
    const row = 32 + index * 37;
    ctx.fillStyle = "#d5e5f6";
    ctx.fillText(signal.name, 10, row + 14, left - 15);
    const events = signal.values[0].time ? [{time: 0, value: "x".repeat(signal.width)}, ...signal.values] : signal.values;
    let priorY;
    for (const [position, {time, value}] of events.entries()) {
      const end = x(events[position + 1]?.time ?? max);
      const start = x(time);
      const bus = signal.width > 1;
      const unknown = /[xz]/.test(value);
      const y = bus || unknown ? row + 10 : row + (value === "1" ? 1 : 20);
      ctx.strokeStyle = unknown ? (value.includes("x") ? "#f1b77d" : "#b6a2ed") : "#76baff";
      ctx.beginPath();
      ctx.moveTo(start, priorY ?? y);
      ctx.lineTo(start, y);
      ctx.lineTo(end, y);
      ctx.stroke();
      if (bus && end - start > 30) {
        ctx.fillStyle = unknown ? "#f1b77d" : "#c2dff9";
        ctx.fillText(value.length > 18 ? value.slice(0, 18) + "…" : value, start + 4, y - 3, end - start - 6);
      }
      priorY = y;
    }
  }
  const cursor = Number(timeCursor.value);
  ctx.strokeStyle = "#ebcb85";
  ctx.beginPath();
  ctx.moveTo(x(cursor), 21);
  ctx.lineTo(x(cursor), height - 9);
  ctx.stroke();
  el("sim-readout").textContent = [cursor + " × " + waveform.unit, ...signals.map(signal => {
    const value = valueAt(signal, cursor);
    return signal.name + " = " + (value.length > 32 ? value.slice(0, 32) + "…" : value);
  })].join("  ·  ");
}

timeCursor.oninput = drawWaves;
new ResizeObserver(entries => {
  if (entries[0].contentRect.width && waveform) drawWaves();
}).observe(canvas.parentElement);

el("sim-download").onclick = () => {
  if (!waveFile) return;
  const url = URL.createObjectURL(new Blob([waveFile.text], {type: "text/plain"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = waveFile.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
