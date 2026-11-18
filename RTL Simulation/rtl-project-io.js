"use strict";

const HDL_SOURCE = /\.(?:v|sv)$/i;
const RTL_FILE = /\.(?:sv|v|svh|vh|mem|hex|mif|dat)$/i;
const SAFE_REF = /^[\w./-]{1,250}$/;
const MAX_FILES = 600;
const MAX_BYTES = 12 * 1024 * 1024;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const encodedPath = path => path.split("/").map(encodeURIComponent).join("/");

function rtlPath(path) {
  return path.length <= 500 && path.split("/").every(part =>
    part && part !== "." && part !== ".." && !/[\u0000-\u001f\\";]/.test(part));
}

function parseRepository(value, revision, scope) {
  let url;
  try {
    url = new URL(/^https:\/\//i.test(value) ? value : "https://github.com/" + value);
  } catch {
    throw new Error("Enter a GitHub repository URL or owner/repository.");
  }
  const [owner, repository, view, urlRef, ...folder] = url.pathname.replace(/^\/|\/$/g, "").split("/").map(decodeURIComponent);
  const repo = repository?.replace(/\.git$/i, "");
  if (url.origin !== "https://github.com" || url.username || url.password ||
      ![owner, repo].every(part => part && /^[\w.-]{1,100}$/.test(part) && ![".", ".."].includes(part)) ||
      (view && (view !== "tree" || !urlRef))) {
    throw new Error("Use an HTTPS GitHub repository URL or a GitHub tree URL.");
  }
  const ref = revision || urlRef || "";
  if (ref && !SAFE_REF.test(ref)) throw new Error("Enter a GitHub branch, tag, or commit SHA.");
  const directory = scope.replace(/^\.\//, "").replace(/\/$/, "") || (!revision ? folder.join("/") : "");
  if (directory && !rtlPath(directory)) throw new Error("Enter a source folder relative to the repository root.");
  return {owner, repo, ref, directory};
}

async function githubResponse(url, token, accept = "application/vnd.github+json") {
  const headers = {Accept: accept};
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch(url, {headers, credentials: "omit", referrerPolicy: "no-referrer",
    signal: AbortSignal.timeout(30000)});
  if (response.status === 401) throw new Error("GitHub rejected the token. Check whether it has expired.");
  if (response.status === 404) throw new Error("GitHub could not find the repository, ref, or file. For a private repository, check the token’s repository access.");
  if (response.status === 429 || (!response.ok && response.headers.get("X-RateLimit-Remaining") === "0")) {
    throw new Error("GitHub’s request limit was reached. Try again later or use a read-only token.");
  }
  if (!response.ok) throw new Error("GitHub could not complete the import (HTTP " + response.status + ").");
  return response;
}

function sourceLimits(files) {
  if (!files.length) throw new Error("No Verilog or SystemVerilog files were found in the selected source folder.");
  if (files.length > MAX_FILES || files.reduce((total, file) => total + (file.size || 0), 0) > MAX_BYTES) {
    throw new Error("This import exceeds 600 files or 12 MB. Select a smaller source folder.");
  }
  if (files.some(file => file.size > MAX_FILE_BYTES)) {
    throw new Error("An RTL file exceeds the 2 MB import limit. Select a smaller source set.");
  }
}

async function loadGitHub(settings, token, progress) {
  const {owner, repo, directory} = settings;
  const endpoint = "https://api.github.com/repos/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo);
  const repositoryUrl = "https://github.com/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo);
  const api = async path => (await githubResponse(endpoint + path, token)).json();
  const ref = settings.ref || (await api("")).default_branch;
  const revision = await api("/commits/" + encodeURIComponent(ref));
  const commit = revision.sha;
  if (!/^[a-f0-9]{40}$/i.test(commit || "")) throw new Error("GitHub did not return a resolved commit.");
  const listing = await api("/git/trees/" + revision.commit.tree.sha + "?recursive=1");
  let tree = listing.tree;
  if (listing.truncated) {
    if (!directory) throw new Error("This repository has a large tree. Enter the RTL source folder and try again.");
    const dir = await api("/contents/" + encodedPath(directory) + "?ref=" + commit);
    if (!Array.isArray(dir)) throw new Error("The GitHub source folder is unavailable.");
    tree = dir.map(item => ({...item, type: item.type === "file" ? "blob" : item.type}));
    if (tree.some(item => item.type === "dir")) {
      throw new Error("This GitHub tree is truncated. Select an RTL folder containing source files directly.");
    }
  }
  const files = tree.filter(file => file.type === "blob" && file.mode !== "120000" &&
    RTL_FILE.test(file.path) && rtlPath(file.path) && (!directory || file.path.startsWith(directory + "/")));
  sourceLimits(files);
  if (!files.some(file => HDL_SOURCE.test(file.path))) throw new Error("The selected GitHub folder has no .v or .sv files.");
  const sources = {};
  let completed = 0;
  for (let offset = 0; offset < files.length; offset += 8) {
    const batch = await Promise.allSettled(files.slice(offset, offset + 8).map(async file => {
      const url = token ? endpoint + "/git/blobs/" + file.sha :
        "https://raw.githubusercontent.com/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo) + "/" + commit + "/" + encodedPath(file.path);
      const response = await githubResponse(url, token, token ? "application/vnd.github.raw+json" : "text/plain");
      sources[file.path] = await response.text();
      progress(++completed, files.length);
    }));
    const failed = batch.find(item => item.status === "rejected");
    if (failed) throw failed.reason;
  }
  return {kind: "github", name: owner + "/" + repo, sources, ref, commit, repositoryUrl};
}

async function loadSelectedFiles(selection, folder = false) {
  const items = Array.from(selection);
  const name = folder ? items[0]?.webkitRelativePath.split("/")[0] || "Local folder" : "Local RTL files";
  const files = items.map(file => ({file, size: file.size,
    path: folder ? file.webkitRelativePath.split("/").slice(1).join("/") : file.name}))
    .filter(file => RTL_FILE.test(file.path) && rtlPath(file.path));
  sourceLimits(files);
  if (new Set(files.map(file => file.path)).size !== files.length) throw new Error("Choose the source folder to preserve duplicate filenames in different directories.");
  if (!files.some(file => HDL_SOURCE.test(file.path))) throw new Error("Choose a folder or files containing .v or .sv sources.");
  const sources = Object.fromEntries(await Promise.all(files.map(async item => [item.path, await item.file.text()])));
  return {kind: "local", name, sources};
}

function indexRtl(sources) {
  const tops = [];
  for (const [file, text] of Object.entries(sources).sort(([a], [b]) => a.localeCompare(b))) {
    if (!HDL_SOURCE.test(file)) continue;
    const withoutComments = text.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|"(?:\\.|[^"\\])*"/g,
      match => match.replace(/[^\n]/g, " "));
    for (const match of withoutComments.matchAll(/\b(?:module|macromodule)\s+(?:(?:automatic|static)\s+)?([A-Za-z_]\w*[\w$]*)/g)) {
      tops.push({name: match[1], file});
    }
  }
  return tops;
}
