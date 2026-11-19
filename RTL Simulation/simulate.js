"use strict";

const el = id => document.getElementById(id);
const consoleOutput = el("sim-console");
const canvas = el("sim-canvas");
const timeCursor = el("sim-time");
let localSources = {};
let waveFile;
let waveform;
const selectedSignals = new Set();

function simStatus(message, error = false) {
  el("sim-status").textContent = message;
  el("sim-status").dataset.error = error;
}

el("sim-open").onclick = () => el("sim-files").click();
el("sim-files").onchange = async event => {
  if (!event.target.files.length) return;
  try {
    const loaded = await loadSelectedFiles(event.target.files);
    localSources = loaded.sources;
    const names = Object.keys(localSources);
    const list = el("sim-input-list");
    list.textContent = "Source files · " + names.join(", ");
    list.hidden = false;
    if (!el("sim-top").value) {
      const modules = indexRtl(localSources);
      el("sim-top").value = modules.find(item => /^tb_|_tb$|testbench/i.test(item.name))?.name ||
        (modules.length === 1 ? modules[0].name : "");
    }
    simStatus("Loaded " + names.length + " source files.");
  } catch (error) {
    simStatus(error.message, true);
  } finally {
    event.target.value = "";
  }
};

el("sim-example").onclick = () => {
  localSources = {};
  el("sim-input-list").hidden = true;
  el("sim-top").value = "tb_dff";
  el("sim-design").value = [
    "module dff(input logic clk, reset, d, output logic q);",
    "  always @(posedge clk) begin",
    "    if (reset) q <= 1'b0;",
    "    else       q <= d;",
    "  end",
    "endmodule"
  ].join("\n");
  el("sim-testbench").value = [
    "`timescale 1ns/1ps",
    "module tb_dff;",
    "  logic clk = 0, reset = 1, d = 0;",
    "  wire q;",
    "  dff dut(.clk(clk), .reset(reset), .d(d), .q(q));",
    "  always #5 clk = ~clk;",
    "  initial begin",
    '    $dumpfile("dff.vcd");',
    "    $dumpvars(0, tb_dff);",
    "    #10 reset = 0; d = 1;",
    "    #10 d = 0;",
    "    #10 d = 1;",
    '    #9 $display("q=%b at the final sample", q);',
    "    $finish;",
    "  end",
    "endmodule"
  ].join("\n");
  simStatus("Flip-flop example loaded.");
};

function runSimulation(sources, top) {
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
      if (data.stage) {
        simStatus(data.stage);
      } else if (data.error) {
        if (data.log) consoleOutput.textContent = data.log;
        complete(new Error(data.error));
      } else {
        complete(null, data.result);
      }
    };
    worker.onerror = () => complete(new Error("The browser simulator stopped unexpectedly."));
    worker.postMessage({sources, top});
  });
}

el("simulation-form").onsubmit = async event => {
  event.preventDefault();
  const sources = {...localSources};
  for (const [id, filename] of [["sim-design", "__cockpit_design.sv"], ["sim-testbench", "__cockpit_testbench.sv"]]) {
    const input = el(id).value;
    if (!input.trim()) continue;
    if (Object.hasOwn(sources, filename)) {
      simStatus("Rename " + filename + " or use its contents as pasted RTL.", true);
      return;
    }
    sources[filename] = input;
  }
  const top = el("sim-top").value.trim();
  if (!Object.keys(sources).some(name => HDL_SOURCE.test(name))) {
    simStatus("Add Verilog or SystemVerilog sources before running a simulation.", true);
    return;
  }
  const controls = ["sim-run", "sim-open", "sim-example"].map(id => el(id));
  for (const control of controls) control.disabled = true;
  consoleOutput.textContent = "Running simulation…";
  displayWaves(null);
  try {
    const result = await runSimulation(sources, top);
    consoleOutput.textContent = result.log || "Testbench completed.";
    displayWaves(result.wave);
    simStatus("Simulation completed for " + top + ".");
  } catch (error) {
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
