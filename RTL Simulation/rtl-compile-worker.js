import {elaborateNetlist} from "./rtl-netlist.js";

// Pinned browser build. Only the RTL source map is passed to the compiler.
const YOSYS = "https://cdn.jsdelivr.net/npm/@yowasp/yosys@0.48.833/gen/bundle.js";
const dir = file => file.slice(0, Math.max(0, file.lastIndexOf("/")));
const quoted = word => '"' + word + '"';

function dependencyFiles(sources, candidates, selected) {
  const definitions = new Map();
  for (const candidate of candidates) {
    if (!definitions.has(candidate.name)) definitions.set(candidate.name, new Set());
    definitions.get(candidate.name).add(candidate.file);
  }
  const files = new Set([selected.file]);
  for (const file of files) {
    const code = sources[file].replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"/g, " ");
    const words = new Set(code.match(/[A-Za-z_]\w*[\w$]*/g) || []);
    for (const name of words) {
      const matches = [...(definitions.get(name) || [])];
      if (matches.some(item => files.has(item))) continue;
      const local = matches.filter(match => dir(match) === dir(file));
      if (local.length === 1) files.add(local[0]);
      else if (matches.length === 1) files.add(matches[0]);
    }
  }
  return [...files];
}

function fileTree(sources) {
  const tree = Object.create(null);
  for (const [path, content] of Object.entries(sources)) {
    const parts = path.split("/");
    const name = parts.pop();
    let folder = tree;
    for (const part of parts) folder = folder[part] ||= Object.create(null);
    folder[name] = content;
  }
  return tree;
}

self.onmessage = async ({data: {sources, candidates, selected}}) => {
  let diagnostics = "";
  try {
    self.postMessage({stage: "Loading the browser RTL compiler…"});
    let compiler;
    try {
      compiler = await import(YOSYS);
    } catch {
      throw new Error("The browser could not load the RTL compiler. Check the connection and try again.");
    }
    const files = dependencyFiles(sources, candidates, selected);
    const folders = [...new Set(Object.keys(sources).map(dir).filter(folder => folder && !/\s/.test(folder)))];
    const includeFlags = folders.map(folder => "-I" + folder).join(" ");
    let output;
    do {
      self.postMessage({stage: "Elaborating " + selected.name + "…"});
      const tree = fileTree(sources);
      tree["__cockpit_script.ys"] = [
        "read_verilog -sv -defer " + includeFlags + " " + files.map(quoted).join(" "),
        "hierarchy -check -top " + selected.name,
        "proc",
        "memory_collect",
        "write_json /__cockpit_netlist.json"
      ].join("\n") + "\n";
      diagnostics = "";
      try {
        const decoder = new TextDecoder();
        output = await compiler.runYosys(["-Q", "-T", "-q", "-s", "__cockpit_script.ys"], tree, {
          stderr: chunk => {
            if (chunk) diagnostics += typeof chunk === "string" ? chunk : decoder.decode(chunk, {stream: true});
          }
        });
        diagnostics += decoder.decode();
        if (!output) throw new Error("The browser compiler stopped before producing output.");
      } catch (error) {
        const missing = /Module `\\?([^'\x60\s]+)' referenced in module/.exec(diagnostics)?.[1];
        const candidatesForModule = candidates.filter(item => item.name === missing && !files.includes(item.file));
        if (candidatesForModule.length === 1) files.push(candidatesForModule[0].file);
        else throw error;
      }
    } while (!output);
    const ret = output["__cockpit_netlist.json"];
    if (!ret) throw new Error("The browser compiler did not produce a netlist.");
    const netlist = JSON.parse(typeof ret === "string" ? ret : new TextDecoder().decode(ret));
    const result = elaborateNetlist(netlist, selected.name, selected.file, sources, diagnostics.trim());
    result.files = files;
    result.filelist = files.map(file => "./" + file).join("\n") + "\n";
    self.postMessage({result});
  } catch (error) {
    self.postMessage({error: diagnostics.trim() || error.message});
  }
};
