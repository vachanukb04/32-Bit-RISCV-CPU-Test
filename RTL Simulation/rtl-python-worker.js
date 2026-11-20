// The cockpit's Slang frontend and Python simulator share this WebAssembly runtime.
const indexURL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";

export async function loadRtlPython(sources) {
  const [runtime, archive] = await Promise.all([
    import(indexURL + "pyodide.mjs"),
    fetch("/rtl-analysis.zip").then(response => {
      if (!response.ok) throw new Error("The cockpit's RTL runtime bundle could not be loaded.");
      return response.arrayBuffer();
    })
  ]);
  const python = await runtime.loadPyodide({indexURL});
  python.FS.mkdirTree("/cockpit");
  python.unpackArchive(archive, "zip", {extractDir: "/cockpit"});
  python.FS.mkdirTree("/project");
  for (const [path, text] of Object.entries(sources)) {
    if (!path.split("/").every(part => part && part !== "." && part !== ".." && !part.includes("\\"))) {
      throw new Error("Invalid project file path.");
    }
    const slash = path.lastIndexOf("/");
    if (slash >= 0) python.FS.mkdirTree("/project/" + path.slice(0, slash));
    python.FS.writeFile("/project/" + path, text);
  }
  return python;
}
