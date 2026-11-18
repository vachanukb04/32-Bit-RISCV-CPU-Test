#!/usr/bin/env python3
"""Serve the RTL workbench and its compile API on the local machine."""

import argparse
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import urlopen


try:
    from compiler import CompileError, compile_design, inventory
except ModuleNotFoundError as exc:
    if exc.name != "pyslang":
        raise
    raise SystemExit("Install dependencies with python3 -m pip install -r 'RTL Simulation/requirements.txt'.") from exc


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GITHUB = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([\w.-]+))?/?", re.ASCII)
ARCHIVE_LIMIT = 32 * 1024 * 1024
ASSETS = {
    "/": ("index.html", "text/html"),
    "/workbench.html": ("workbench.html", "text/html"),
    "/cockpit.js": ("cockpit.js", "text/javascript"),
    "/semiconductor-marks.webp": ("semiconductor-marks.webp", "image/webp"),
    "/workbench.css": ("workbench.css", "text/css"),
    "/schematic-graph.js": ("schematic-graph.js", "text/javascript"),
    "/schematic-layout.js": ("schematic-layout.js", "text/javascript"),
    "/schematic.js": ("schematic.js", "text/javascript"),
    "/workbench.js": ("workbench.js", "text/javascript"),
    "/workbench-projects.js": ("workbench-projects.js", "text/javascript"),
    "/rtl-project-io.js": ("rtl-project-io.js", "text/javascript"),
    "/rtl-compile-worker.js": ("rtl-compile-worker.js", "text/javascript"),
    "/rtl-netlist.js": ("rtl-netlist.js", "text/javascript"),
}


def project_path(value, clones):
    if not value or value == ".":
        return REPO
    match = GITHUB.fullmatch(value)
    if match and all(part not in (".", "..") for part in match.groups() if part):
        owner, repo, ref = match.groups()
        target = clones / owner / repo / (("ref-" + ref) if ref else "default")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=target.parent) as staging:
                checkout = Path(staging) / "checkout"
                branch = [f"--branch={ref}"] if ref else []
                proc = subprocess.run(
                    ["git", "-c", "credential.interactive=false", "clone", "--depth", "1", *branch, "--",
                     f"https://github.com/{owner}/{repo}.git", str(checkout)],
                    text=True, capture_output=True, timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if proc.returncode:
                    raise CompileError("GitHub clone failed:\n" + proc.stderr.strip())
                checkout.rename(target)
        return target
    if "://" in value:
        raise CompileError("Enter a public GitHub repository URL or a /tree/branch URL. Use Source directory for subfolders.")
    resolved = (REPO / Path(value).expanduser()).resolve()
    if not resolved.is_dir():
        raise CompileError(f"Directory not found: {resolved}")
    return resolved


def source_directory(root, scope):
    selected = (root / (scope or ".")).resolve()
    if not selected.is_relative_to(root) or not selected.is_dir():
        raise CompileError("Source directory must be a directory within the RTL project.")
    return selected


class Workbench(BaseHTTPRequestHandler):
    def __init__(self, *args, clones, **kwargs):
        self.clones = clones
        super().__init__(*args, **kwargs)

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_body(status, json.dumps(value, ensure_ascii=True).encode(), "application/json")

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/github-archive":
            self.send_github_archive(url.query)
            return
        if url.path not in ASSETS:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset, mime = ASSETS[url.path]
        self.send_body(HTTPStatus.OK, (HERE / asset).read_bytes(), mime)

    def send_github_archive(self, query):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Open the repository from the workbench."})
            return
        parameters = parse_qs(query)
        owner, repo, ref = (parameters.get(key, [default])[0] for key, default in
                            (("owner", ""), ("repo", ""), ("ref", "HEAD")))
        if (any(not re.fullmatch(r"[\w.-]{1,100}", part, re.ASCII) or part in (".", "..") for part in (owner, repo))
                or not re.fullmatch(r"[\w./-]{1,250}", ref, re.ASCII)
                or any(part in ("", ".", "..") for part in ref.split("/"))):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Use a valid GitHub repository and revision."})
            return
        target = f"https://codeload.github.com/{owner}/{repo}/zip/{quote(ref)}"
        try:
            with urlopen(target, timeout=20) as response:
                if response.headers.get_content_type() != "application/zip":
                    self.send_json(HTTPStatus.BAD_GATEWAY, {"error": "GitHub did not return a repository ZIP archive."})
                    return
                archive = response.read(ARCHIVE_LIMIT + 1)
        except HTTPError as error:
            self.send_json(error.code, {"error": "GitHub could not download the public repository archive."})
            return
        except (URLError, OSError):
            self.send_json(HTTPStatus.BAD_GATEWAY, {"error": "GitHub’s archive download could not be reached."})
            return
        if len(archive) > ARCHIVE_LIMIT:
            self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "The public repository archive exceeds 32 MB."})
            return
        self.send_body(HTTPStatus.OK, archive, "application/zip")

    def do_POST(self):
        if self.path not in ("/api/scan", "/api/compile"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if (host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                or (origin and origin != f"http://{host}")):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16_384:
                raise CompileError("Invalid request size.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
                raise CompileError("Expected project and source settings as text.")
            project = data.get("project", "").strip()
            root = project_path(project, self.clones)
            scope = source_directory(root, data.get("scope", "").strip())
            if self.path == "/api/scan":
                sources, _, _, tops = inventory(root, scope)
                result = {"count": len(sources), "tops": tops,
                          "directories": sorted({p.parent.relative_to(root).as_posix() for p in sources})}
            else:
                result = compile_design(root, scope, data.get("top"), data.get("file"))
            self.send_json(HTTPStatus.OK, result)
        except (ValueError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="rtl-workbench-") as temp:
        handler = partial(Workbench, clones=Path(temp))
        with HTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Open http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
