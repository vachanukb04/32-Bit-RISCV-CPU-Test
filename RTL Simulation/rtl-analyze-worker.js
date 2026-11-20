import {loadRtlPython} from "./rtl-python-worker.js";

self.onmessage = async ({data: {sources, selected}}) => {
  try {
    self.postMessage({stage: "Loading the Slang RTL analyzer…"});
    const python = await loadRtlPython(sources);

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
