import {loadRtlPython} from "./rtl-python-worker.js";

self.onmessage = async ({data: {sources, top, settings}}) => {
  try {
    self.postMessage({loading: true});
    const python = await loadRtlPython(sources);
    python.globals.set("run_config", JSON.stringify({files: Object.keys(sources), top, ...settings}));
    python.globals.set("report_stage", stage => self.postMessage({stage}));
    python.globals.set("report_action", (stage, action, detail) => self.postMessage({stage, action, detail}));
    const output = python.runPython(`
import json
import sys

sys.path.insert(0, "/cockpit")
from rtl_sim_cli import run_project

config = json.loads(run_config)
response = run_project("/project", report=report_stage, progress=report_action, **config)
json.dumps(response)
`);
    self.postMessage(JSON.parse(output));
  } catch (error) {
    self.postMessage({error: error.message});
  }
};
