const archiveLimit = 32 * 1024 * 1024;
const cacheControl = "no-store";

function archiveError(status, error) {
  return Response.json({error}, {status, headers: {"Cache-Control": cacheControl}});
}

async function githubArchive(request, url) {
  if (request.headers.get("Sec-Fetch-Site") === "cross-site") {
    return archiveError(403, "Open the repository from the workbench.");
  }
  const {owner = "", repo = "", ref = "HEAD"} = Object.fromEntries(url.searchParams);
  if (![owner, repo].every(part => /^[\w.-]{1,100}$/.test(part) && ![".", ".."].includes(part)) ||
      !/^[\w./-]{1,250}$/.test(ref) || ref.split("/").some(part => !part || part === "." || part === "..")) {
    return archiveError(400, "Use a valid GitHub repository and revision.");
  }

  const revision = ref.split("/").map(encodeURIComponent).join("/");
  const target = "https://codeload.github.com/" + encodeURIComponent(owner) + "/" +
    encodeURIComponent(repo) + "/zip/" + revision;
  let archive;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    archive = await fetch(target, {redirect: "manual", signal: controller.signal});
  } catch (error) {
    console.warn("GitHub public archive request failed", error.message);
    return archiveError(502, "GitHub’s archive download could not be reached.");
  } finally {
    clearTimeout(timer);
  }
  if (archive.status === 404) return archiveError(404, "GitHub could not find a public archive for this repository and revision.");
  if (!archive.ok || !archive.body || !archive.headers.get("Content-Type")?.includes("application/zip")) {
    return archiveError(502, "GitHub did not return a repository ZIP archive.");
  }
  if (Number(archive.headers.get("Content-Length")) > archiveLimit) {
    await archive.body.cancel();
    return archiveError(413, "The public repository archive exceeds 32 MB.");
  }

  let received = 0;
  const limited = new TransformStream({
    transform(chunk, controller) {
      received += chunk.byteLength;
      if (received > archiveLimit) controller.error(new Error("Archive exceeds 32 MB."));
      else controller.enqueue(chunk);
    }
  });
  return new Response(archive.body.pipeThrough(limited), {headers: {
    "Content-Type": "application/zip", "Cache-Control": cacheControl, "X-Content-Type-Options": "nosniff"
  }});
}

export function createCockpitWorker(assets) {
  return {
    async fetch(request) {
      const url = new URL(request.url);
      if (url.pathname === "/api/github-archive" && request.method === "GET") return githubArchive(request, url);
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response("Method not allowed", {status: 405, headers: {Allow: "GET, HEAD"}});
      }
      const pathname = {"/": "/index.html", "/workbench": "/workbench.html", "/workbench/": "/workbench.html",
        "/simulate": "/simulate.html", "/simulate/": "/simulate.html"}[url.pathname] || url.pathname;
      if (!Object.hasOwn(assets, pathname)) return new Response("Not found", {status: 404});
      const [contentType, value, binary] = assets[pathname];
      const body = request.method === "HEAD" ? null : binary ? decodeAsset(value) : value;
      return new Response(body, {headers: {
        "Content-Type": contentType, "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"
      }});
    }
  };
}

function decodeAsset(value) {
  if (typeof Uint8Array.fromBase64 === "function") return Uint8Array.fromBase64(value);
  const decoded = atob(value);
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index++) bytes[index] = decoded.charCodeAt(index);
  return bytes;
}
