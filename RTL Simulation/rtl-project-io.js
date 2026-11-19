"use strict";

const HDL_SOURCE = /\.(?:v|sv)$/i;
const RTL_FILE = /\.(?:sv|v|svh|vh|mem|hex|mif|dat)$/i;
const SAFE_REF = /^[\w./-]{1,250}$/;
const MAX_FILES = 600;
const MAX_BYTES = 12 * 1024 * 1024;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const encodedPath = path => path.split("/").map(encodeURIComponent).join("/");
class GitHubQuotaError extends Error {}
class GitHubArchiveError extends Error {}

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
    throw new GitHubQuotaError("GitHub’s API request limit was reached. Try again after the limit resets or use a read-only token.");
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

function archiveIndex(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder();
  const u16 = at => view.getUint16(at, true);
  const u32 = at => view.getUint32(at, true);
  const invalid = () => new GitHubArchiveError("GitHub’s ZIP archive could not be read.");
  let end = bytes.length - 22;
  while (end >= Math.max(0, bytes.length - 65557) &&
    (u32(end) !== 0x06054b50 || end + 22 + u16(end + 20) !== bytes.length)) end--;
  if (end < Math.max(0, bytes.length - 65557)) throw invalid();
  const commit = decoder.decode(bytes.subarray(end + 22)).trim();
  if (!/^[a-f0-9]{40}$/i.test(commit)) throw new GitHubArchiveError("GitHub’s archive did not identify its commit.");

  const centralEnd = u32(end + 16) + u32(end + 12);
  let record = u32(end + 16);
  const count = u16(end + 10);
  if (centralEnd !== end || count === 0xffff || u16(end + 4) || u16(end + 6)) throw invalid();
  const files = [];
  for (let i = 0; i < count; i++) {
    if (record + 46 > centralEnd || u32(record) !== 0x02014b50) throw invalid();
    const nameSize = u16(record + 28);
    const name = decoder.decode(bytes.subarray(record + 46, record + 46 + nameSize));
    const slash = name.indexOf("/");
    const path = slash > 0 ? name.slice(slash + 1) : "";
    const symlink = ((u32(record + 38) >>> 16) & 0xf000) === 0xa000;
    if (!symlink && RTL_FILE.test(path) && rtlPath(path)) {
      files.push({path, flags: u16(record + 8), method: u16(record + 10), packed: u32(record + 20),
        size: u32(record + 24), offset: u32(record + 42)});
    }
    record += 46 + nameSize + u16(record + 30) + u16(record + 32);
  }
  if (record !== centralEnd) throw invalid();
  return {commit, files, end: u32(end + 16)};
}

async function archiveText(bytes, entry, end) {
  const {offset, packed, size, method, flags} = entry;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const invalid = () => new GitHubArchiveError("A file in GitHub’s ZIP archive could not be read.");
  if (offset + 30 > end || view.getUint32(offset, true) !== 0x04034b50 || flags & 1) throw invalid();
  const start = offset + 30 + view.getUint16(offset + 26, true) + view.getUint16(offset + 28, true);
  if (start + packed > end || ![0, 8].includes(method)) throw invalid();
  const raw = bytes.subarray(start, start + packed);
  const decoder = new TextDecoder();
  if (method === 0) {
    if (raw.length !== size) throw invalid();
    return decoder.decode(raw);
  }
  const expanded = new Uint8Array(size);
  let written = 0;
  try {
    const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    for await (const chunk of stream) {
      if (written + chunk.byteLength > size) throw invalid();
      expanded.set(chunk, written);
      written += chunk.byteLength;
    }
  } catch {
    throw invalid();
  }
  if (written !== size) throw invalid();
  return decoder.decode(expanded);
}

function gitHubProject(settings, ref, commit, sources) {
  const {owner, repo} = settings;
  const repositoryUrl = "https://github.com/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo);
  return {kind: "github", name: owner + "/" + repo, sources, ref, commit, repositoryUrl};
}

async function loadGitHubArchive(settings, progress) {
  const {owner, repo, directory} = settings;
  const ref = settings.ref || "HEAD";
  const query = new URLSearchParams({owner, repo, ref});
  let bytes;
  try {
    const response = await fetch("/api/github-archive?" + query, {cache: "no-store", signal: AbortSignal.timeout(30000)});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new GitHubArchiveError(payload.error || "The public archive could not be downloaded.");
    }
    bytes = new Uint8Array(await response.arrayBuffer());
  } catch (error) {
    if (error instanceof GitHubArchiveError) throw error;
    throw new GitHubArchiveError("The public archive download did not complete.");
  }
  const {commit, files, end} = archiveIndex(bytes);
  const selected = files.filter(file => !directory || file.path.startsWith(directory + "/"));
  sourceLimits(selected);
  if (!selected.some(file => HDL_SOURCE.test(file.path))) throw new Error("The selected GitHub folder has no .v or .sv files.");
  if (new Set(selected.map(file => file.path)).size !== selected.length) {
    throw new GitHubArchiveError("The public archive contains duplicate RTL paths.");
  }
  const sources = {};
  for (const [i, file] of selected.entries()) {
    sources[file.path] = await archiveText(bytes, file, end);
    progress(i + 1, selected.length);
  }
  return gitHubProject(settings, ref, commit, sources);
}

async function loadGitHubApi(settings, token, progress) {
  const {owner, repo, directory} = settings;
  const endpoint = "https://api.github.com/repos/" + encodeURIComponent(owner) + "/" + encodeURIComponent(repo);
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
  return gitHubProject(settings, ref, commit, sources);
}

async function loadGitHub(settings, token, progress) {
  if (token) return loadGitHubApi(settings, token, progress);
  let archiveError;
  try {
    return await loadGitHubArchive(settings, progress);
  } catch (error) {
    if (!(error instanceof GitHubArchiveError)) throw error;
    archiveError = error;
  }
  try {
    return await loadGitHubApi(settings, "", progress);
  } catch (error) {
    if (error instanceof GitHubQuotaError) throw new Error(archiveError.message + " " + error.message);
    throw error;
  }
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

function reviewedSimulation(project, selected, analysis) {
  const analyzed = analysis?.hierarchy?.source.file === selected.file && analysis.top === selected.name
    ? analysis : null;
  const sourceText = {...project.sources, ...analyzed?.sourceText};
  const directory = selected.file.slice(0, selected.file.lastIndexOf("/") + 1);
  const duplicated = new Set();
  const seen = new Map();
  for (const {name, file} of project.candidates) {
    if (seen.has(name) && seen.get(name) !== file) duplicated.add(name);
    else seen.set(name, file);
  }
  const analyzedFiles = new Set(analyzed?.files);
  const preferred = new Map();
  const excluded = new Set();
  for (const {name, file} of project.candidates) {
    if (!duplicated.has(name)) continue;
    const rank = file === selected.file ? 3 : analyzedFiles.has(file) ? 2 :
      file.slice(0, file.lastIndexOf("/") + 1) === directory ? 1 : 0;
    if (!preferred.has(name) || preferred.get(name).rank < rank) preferred.set(name, {file, rank});
  }
  for (const {name, file} of project.candidates) {
    const match = preferred.get(name);
    if (match?.rank && match.file !== file) excluded.add(file);
  }
  const ordered = new Set([...(analyzed?.files || []), ...Object.keys(sourceText)]);
  const sources = Object.fromEntries([...ordered].filter(file => {
    if (!Object.hasOwn(sourceText, file) || excluded.has(file)) return false;
    // With parallel RTL trees, use the chosen variant and its analyzed dependencies.
    return !duplicated.size || analyzedFiles.has(file) || !HDL_SOURCE.test(file) ||
      file.slice(0, file.lastIndexOf("/") + 1) === directory;
  }).map(file => [file, sourceText[file]]));
  const names = new Set(indexRtl(sources).map(item => item.name));
  const testbench = /^(?:tb_|testbench_)|(?:_tb|_testbench)$/i.test(selected.name) ? selected.name :
    ["tb_" + selected.name, selected.name + "_tb", "testbench_" + selected.name, selected.name + "_testbench"]
      .find(name => names.has(name)) || "";
  return {name: project.name, top: selected.name, testbench, sources};
}
