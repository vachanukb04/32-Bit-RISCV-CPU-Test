// The Sites Worker serves the existing static cockpit and the public archive route.
import {mkdir, readdir, readFile, writeFile} from "node:fs/promises";
import {dirname, extname, join} from "node:path";

const [source, output] = process.argv.slice(2);
if (!source || !output) throw new Error("Usage: node build-cockpit-worker.mjs <static-dir> <worker-file>");

const types = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
  ".json": "application/json", ".webp": "image/webp", ".zip": "application/zip"};
const assets = {};
for (const file of (await readdir(source, {withFileTypes: true})).filter(entry => entry.isFile()).sort((a, b) => a.name.localeCompare(b.name))) {
  const extension = extname(file.name);
  if (!Object.hasOwn(types, extension)) continue;
  const binary = [".webp", ".zip"].includes(extension);
  const value = await readFile(join(source, file.name), binary ? "base64" : "utf8");
  assets["/" + file.name] = [types[extension] + (binary ? "" : "; charset=utf-8"), value, binary];
}
for (const required of ["/index.html", "/workbench.html", "/simulate.html", "/rtl-sim-worker.js",
  "/rtl-python-worker.js", "/rtl-analysis.zip", "/compiled-designs.json"]) {
  if (!assets[required]) throw new Error("Missing hosted asset " + required);
}

const template = await readFile(new URL("./github-archive-worker.mjs", import.meta.url), "utf8");
await mkdir(dirname(output), {recursive: true});
await writeFile(output, template + "\nexport default createCockpitWorker(" + JSON.stringify(assets) + ");\n");
