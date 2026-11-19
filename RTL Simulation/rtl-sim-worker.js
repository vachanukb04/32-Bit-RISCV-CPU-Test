// Icarus Verilog WebAssembly toolchain from the VeriSim port, pinned to a release commit.
const toolchain = "https://cdn.jsdelivr.net/gh/senolgulgonul/verisim@ab0ff3862a8cf8c6535117a64d370c4bf263042f/";
const sourceType = /\.(?:v|sv)$/i;
const limit = 4 * 1024 * 1024;

function writeFiles(module, sources) {
  for (const [name, text] of Object.entries(sources)) {
    const end = name.lastIndexOf("/");
    if (end >= 0) module.FS.mkdirTree("/" + name.slice(0, end));
    module.FS.writeFile("/" + name, text.endsWith("\n") ? text : text + "\n");
  }
}

function invoke(module, args) {
  try {
    return module.callMain(args) ?? 0;
  } catch (error) {
    if (Number.isInteger(error?.status)) return error.status;
    throw error;
  }
}

function simConfig(top) {
  return [
    "basedir:/", "module:system.vpi", "generation:2012", "generation:no-specify", "root:" + top,
    "out:/__cockpit_sim.vvp", "iwidth:32", "widthcap:65536", "functor:cprop", "functor:nodangle", "flag:DLL=vvp.tgt"
  ].join("\n") + "\n";
}

function vcdOutput(module, folder = "/", depth = 0) {
  if (depth > 8) return null;
  for (const name of module.FS.readdir(folder)) {
    if (name === "." || name === ".." || (depth === 0 && ["dev", "proc", "tmp"].includes(name))) continue;
    const path = folder + name;
    const info = module.FS.stat(path);
    if (module.FS.isDir(info.mode)) {
      const nested = vcdOutput(module, path + "/", depth + 1);
      if (nested) return nested;
    } else if (/\.vcd$/i.test(name)) {
      if (info.size > limit) throw new Error("The VCD exceeds 4 MB. Dump fewer signals or shorten the simulation.");
      return {name, text: module.FS.readFile(path, {encoding: "utf8"})};
    }
  }
  return null;
}

self.onmessage = async ({data: {sources, top}}) => {
  const messages = [];
  let logLength = 0;
  function log(text) {
    if (logLength >= 100000) return;
    const line = String(text);
    logLength += line.length;
    messages.push(line.slice(0, 4000));
  }
  function diagnostics() {
    // The port registers standard system tasks statically and also tries to load them dynamically.
    return messages.join("\n").replace(/^error: Failed to open 'system\.vpi' because:\n[ \t]*: dynamic linking not enabled\n?/gm, "");
  }
  try {
    if (!/^[A-Za-z_][\w$]*$/.test(top)) throw new Error("Enter a valid testbench top module.");
    self.postMessage({stage: "Loading the Icarus Verilog simulator…"});
    const [preprocess, compile, simulate] = await Promise.all(["ivlpp", "ivl", "vvp"].map(async tool =>
      (await import(toolchain + tool + ".js")).default));

    self.postMessage({stage: "Compiling simulation top " + top + "…"});
    const text = [];
    const preprocessor = await preprocess({noInitialRun: true, print: line => text.push(line), printErr: log});
    writeFiles(preprocessor, sources);
    const inputs = Object.keys(sources).filter(name => sourceType.test(name)).map(name => "/" + name);
    if (invoke(preprocessor, ["-L", ...inputs])) throw new Error("Verilog preprocessing failed.");
    const compiler = await compile({noInitialRun: true, print: log, printErr: log});
    compiler.FS.writeFile("/__cockpit_ivl.conf", simConfig(top));
    compiler.FS.writeFile("/__cockpit_combined.sv", text.join("\n") + "\n");
    if (invoke(compiler, ["-C/__cockpit_ivl.conf", "--", "/__cockpit_combined.sv"])) {
      throw new Error("Icarus Verilog could not compile the testbench.");
    }
    const executable = compiler.FS.readFile("/__cockpit_sim.vvp");

    self.postMessage({stage: "Running " + top + "…"});
    const runtime = await simulate({noInitialRun: true, print: log, printErr: log});
    writeFiles(runtime, sources);
    runtime.FS.writeFile("/__cockpit_sim.vvp", executable);
    if (invoke(runtime, ["/__cockpit_sim.vvp"])) throw new Error("The simulation exited with an error.");
    self.postMessage({result: {log: diagnostics(), wave: vcdOutput(runtime)}});
  } catch (error) {
    self.postMessage({error: error.message, log: diagnostics()});
  }
};
