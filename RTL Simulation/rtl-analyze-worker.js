// Pyodide hosts the same Slang-based analyzer used by the local workbench.
const indexURL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const enginePath = "/rtl-analysis.zip";

self.onmessage = async ({data: {sources, selected}}) => {
  try {
    self.postMessage({stage: "Loading the Slang RTL analyzer…"});
    const [runtime, archive] = await Promise.all([
      import(indexURL + "pyodide.mjs"),
      fetch(enginePath).then(response => {
        if (!response.ok) throw new Error("The Slang analyzer bundle could not be loaded.");
        return response.arrayBuffer();
      })
    ]);
    const python = await runtime.loadPyodide({indexURL});
    python.FS.mkdirTree("/cockpit");
    python.unpackArchive(archive, "zip", {extractDir: "/cockpit"});
    python.FS.mkdirTree("/project");
    for (const [path, text] of Object.entries(sources)) {
      const slash = path.lastIndexOf("/");
      if (slash >= 0) python.FS.mkdirTree("/project/" + path.slice(0, slash));
      python.FS.writeFile("/project/" + path, text);
    }

    self.postMessage({stage: "Analyzing " + selected.name + " with Slang…"});
    python.globals.set("selected_top", selected.name);
    python.globals.set("selected_source", selected.file);
    const output = python.runPython(`
import json
import sys
from pathlib import Path

sys.path.insert(0, "/cockpit")
from compiler import CompileError, compile_design

try:
    model = compile_design(Path("/project"), Path("/project"), selected_top, selected_source)
    model["engine"] = "Slang"
    response = {"result": model}
except (CompileError, OSError) as error:
    response = {"error": str(error)}
json.dumps(response)
`);
    self.postMessage(JSON.parse(output));
  } catch (error) {
    self.postMessage({error: error.message});
  }
};
