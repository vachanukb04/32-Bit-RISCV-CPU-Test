#!/usr/bin/env python3
"""Initialize, analyze, and elaborate supported Verilog RTL without an EDA tool."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from rtl_analysis import FlowError, IDENT, Parser, Token, preprocess, read_inputs, tokenize_line
from rtl_elaboration import Elaborator, Scope, expr, require_constant


SCHEMA_VERSION = 1
OWNER_FILE = ".init_elab_output.json"
INIT_DESIGN = Path(__file__).resolve().parent
DEFAULT_REPORTS = INIT_DESIGN / "reports"
DEFAULT_LOG = INIT_DESIGN / "logs" / "init_design.log"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_text(source):
    return f"{source['file']}:{source['line']}:{source['column']}" if source else "command line"


def format_type(dtype):
    sign = "signed" if dtype["signed"] else "unsigned"
    dims = "".join(f" [{left}:{right}]" for left, right in dtype["dimensions"])
    packed = dtype["packed"]
    bit_range = f" [{packed[0]}:{packed[1]}]" if packed else ""
    return f"{dtype['width']}-bit {sign}{bit_range}{dims}"


def format_expr(item):
    if item is None:
        return "(open)"
    kind = item["kind"]
    if kind == "name":
        return item.get("symbol", item["name"])
    if kind == "literal":
        return item["text"]
    if kind == "resize":
        return format_expr(item["value"])
    if kind == "index":
        return f"{format_expr(item['value'])}[{format_expr(item['index'])}]"
    if kind == "slice":
        return (f"{format_expr(item['value'])}"
                f"[{format_expr(item['msb'])}:{format_expr(item['lsb'])}]")
    if kind in ("concat", "replicate"):
        parts = "{" + ", ".join(format_expr(part) for part in item["parts"]) + "}"
        return "{" + str(item["repeat"]) + parts + "}" if kind == "replicate" else parts
    if kind == "binary":
        return f"({format_expr(item['left'])} {item['op']} {format_expr(item['right'])})"
    if kind == "unary":
        return f"{item['op']}{format_expr(item['operand'])}"
    if kind == "ternary":
        return (f"({format_expr(item['condition'])} ? {format_expr(item['yes'])} : "
                f"{format_expr(item['no'])})")
    if kind == "call":
        return f"{item['name']}({format_expr(item['argument'])})"
    return kind


def process_items(items):
    for item in items:
        if item["kind"] == "process":
            yield item
        elif item["kind"] == "generate_scope":
            yield from process_items(item["items"])


def block_reports(design, diagnostics, directory):
    instances = defaultdict(list)
    for item in design["instances"]:
        instances[item["definition"]].append(item["path"])
    directory.mkdir()
    for key, block in design["definitions"].items():
        lines = [f"Definition {key} ({block['kind']})",
                 f"Module {block['module']} at {source_text(block['source'])}",
                 "Instances", *(f"  {path}" for path in instances[key]), "", "Parameters"]
        lines.extend(f"  {name} = {value['value']} ({format_type(value['type'])})"
                     for name, value in block["parameters"].items())
        lines.extend(("", "Ports"))
        lines.extend(f"  {p['direction']:6} {p['name']:18} {format_type(p['type'])}"
                     for p in block["ports"])
        if block.get("power_pins"):
            lines.append("  Power pins: " + ", ".join(block["power_pins"]))
        lines.extend(("", "Signals and arrays"))
        lines.extend(f"  {s['storage']:7} {s['name']:18} {format_type(s['type'])}"
                     for s in block["signals"])
        lines.extend(("", "Child instances"))
        for child in block["children"]:
            lines.append(f"  {child['instance']} : {child['definition']}")
            lines.extend(f"    .{b['port']}({format_expr(b['actual'])})"
                         f" [{b['direction']}, {b['type']['width']} bit(s)]"
                         for b in child["bindings"])
        lines.extend(("", "Procedural event controls"))
        for item in process_items(block["items"]):
            events = ", ".join(
                "@*" if event["edge"] == "wildcard"
                else f"{event['edge']} {format_expr(event['expression'])}"
                for event in item["events"])
            lines.append(f"  {item['process']} {events} at {source_text(item['source'])}")
        lines.extend(("", "Diagnostics"))
        lines.extend(f"  {source_text(d['source'])}: {d['message']}" for d in diagnostics
                     if d.get("definition") == key)
        (directory / f"{key}.rpt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def hierarchy_report(design, diagnostics):
    definitions = design["definitions"]
    instances = design["instances"]
    child_count = len(instances) - 1
    bound = [b for row in instances for b in row["bindings"] if b["actual"] is not None]
    open_ports = sum(b["actual"] is None for row in instances for b in row["bindings"])
    lines = [
        f"Elaborated RTL: {design['top']}",
        f"Definitions: {len(definitions)}   Child instances: {child_count}",
        f"Width-checked port bindings: {len(bound)}   Open ports: {open_ports}",
        "", "Hierarchy and port bindings",
    ]
    levels = {}
    for row in instances:
        depth = levels[row["parent"]] + 1 if row["parent"] else 0
        levels[row["path"]] = depth
        pad = "  " * depth
        lines.append(f"{pad}{row['path']} : {row['definition']} ({row['kind']})"
                     f" at {source_text(row['source'])}")
        lines.extend(f"{pad}  {b['direction']:6} {b['port']:14} "
                     f"{format_type(b['type'])} -> {format_expr(b['actual'])}"
                     for b in row["bindings"])
    lines.extend(("", "Definition reports"))
    lines.extend(f"  blocks/{key}.rpt" for key in sorted(definitions))
    lines.extend(("", "Diagnostics"))
    lines.extend(f"  {d['severity']}: {source_text(d['source'])}: {d['message']}" for d in diagnostics)
    if not diagnostics:
        lines.append("  None")
    return "\n".join(lines) + "\n"


def prior_reports(output):
    """Allow a rerun to replace only a dedicated output directory we own."""
    if not output.exists():
        return False
    if not output.is_dir():
        raise FlowError(f"Output path is not a directory: {output}")
    if not any(output.iterdir()):
        return True
    marker = output / OWNER_FILE
    try:
        previous = json.loads(marker.read_text(encoding="utf-8"))
        owned = set(previous["files"])
        if previous["owner"] != "riscv-init-elab":
            raise ValueError("unexpected report owner")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FlowError(f"Choose an empty or previous init_elab output directory: {output}") from exc
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*")
              if not p.is_dir() or p.is_symlink()}
    if (not actual <= owned | {OWNER_FILE}
            or any(p.is_symlink() or (p.is_dir() and p != output / "blocks")
                   for p in output.rglob("*"))):
        raise FlowError(f"Output directory contains files outside the report set: {output}")
    return True


def publish(output, documents, design, diagnostics):
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = prior_reports(output)
    with TemporaryDirectory(prefix=".init_elab_", dir=output.parent) as working:
        work = Path(working)
        reports = work / "reports"
        reports.mkdir()
        for name, document in documents.items():
            write_json(reports / name, document)
        if design is not None:
            block_reports(design, diagnostics, reports / "blocks")
            (reports / "elaboration.rpt").write_text(hierarchy_report(design, diagnostics),
                                                     encoding="utf-8")
        owned = sorted(p.relative_to(reports).as_posix() for p in reports.rglob("*") if p.is_file())
        write_json(reports / OWNER_FILE, {"owner": "riscv-init-elab", "files": owned})
        backup = work / "previous"
        if previous:
            output.rename(backup)
        try:
            reports.rename(output)
        except OSError:
            if previous:
                backup.rename(output)
            raise


def parameter_overrides(raw):
    values = {}
    for number, text in enumerate(raw, 1):
        name, sep, value = text.partition("=")
        if not sep or not re.fullmatch(IDENT, name) or not value or name in values:
            raise FlowError(f"Expected a unique --parameter NAME=EXPR, got {text!r}")
        file = "<command-line>"
        tokens = [*tokenize_line(value, file, number), Token("<eof>", file, number, len(value) + 1)]
        parser = Parser(tokens)
        parsed = expr(parser.expr(), Scope())
        parser.expect("<eof>")
        require_constant(parsed, f"Override of {name}")
        values[name] = parsed
    return values


def run(options, log):
    docs, diagnostics = {}, []
    stage, design = "init", None

    def trace(message):
        log(stage, message)

    log("run", f"Starting init_design for top {options.top}")
    try:
        trace("Begin initialization and source discovery")
        root, sources, include_dirs, defines, interfaces, manifest = read_inputs(
            options.filelist, options.top, options.interfaces, trace)
        overrides = parameter_overrides(options.parameter)
        manifest["parameter_overrides"] = options.parameter
        docs["init.json"] = {"schema_version": SCHEMA_VERSION, **manifest}
        trace(f"Initialization complete: {len(sources)} RTL files, {len(include_dirs)} include "
              f"directories, {len(defines)} defines, {len(interfaces)} explicit cell or macro interfaces")
        if overrides:
            trace(f"Top parameter overrides: {', '.join(overrides)}")
        stage = "analyze"
        trace("Begin RTL front-end compilation: preprocess, tokenize, and parse")
        tokens, dependencies, timescales = preprocess(root, sources, include_dirs, defines, trace)
        trace(f"Tokenization complete: {len(tokens) - 1} tokens from {len(dependencies)} source dependencies")
        modules = Parser(tokens).modules(trace)
        docs["analysis.json"] = {"schema_version": SCHEMA_VERSION, "top": options.top,
                                 "dependencies_sha256": dependencies, "timescales": timescales,
                                 "modules": modules}
        trace(f"Analysis complete: indexed {len(modules)} module definitions")
        stage = "elab"
        trace(f"Begin elaboration from {options.top}")
        elaborator = Elaborator(modules, interfaces, trace)
        diagnostics = elaborator.warnings
        design = elaborator.elaborate(options.top, overrides)
        design["diagnostics"] = diagnostics
        docs["elaboration.json"] = design
        bindings = [binding for instance in design["instances"] for binding in instance["bindings"]]
        open_ports = sum(binding["actual"] is None for binding in bindings)
        trace(f"Elaboration complete: {len(design['definitions'])} definitions, "
              f"{len(design['instances']) - 1} child instances, "
              f"{len(bindings) - open_ports} width-checked bindings, {open_ports} open ports")
    except FlowError as exc:
        diagnostics.append({"severity": "error", "stage": stage, "source": exc.source,
                            "instance_path": exc.instance_path, "message": str(exc)})
    except RecursionError:
        diagnostics.append({"severity": "error", "stage": stage, "source": None,
                            "message": "Input exceeds supported parsing or elaboration depth"})
    docs["diagnostics.json"] = {"schema_version": SCHEMA_VERSION, "top": options.top,
                               "status": "ok" if design else "failed",
                               "stage": stage, "diagnostics": diagnostics}
    for diagnostic in diagnostics:
        instance = diagnostic.get("instance_path")
        context = f" [{instance}]" if instance else ""
        log(diagnostic["stage"], f"{source_text(diagnostic['source'])}{context}: "
            f"{diagnostic['message']}", diagnostic["severity"])
        print(f"{source_text(diagnostic['source'])}: {diagnostic['stage']} "
              f"{diagnostic['severity']}{context}: {diagnostic['message']}")
    log("report", "Writing " + ", ".join(docs))
    publish(options.out.resolve(), docs, design, diagnostics)
    if design is None:
        log("run", f"Status: FAILED during {stage}", "error")
        print(f"See {options.out / 'diagnostics.json'}")
        return 1
    log("report", f"Wrote elaboration.rpt and {len(design['definitions'])} block reports")
    log("run", f"Status: SUCCESS ({len(diagnostics)} diagnostics)")
    print(f"Elaborated {options.top}: {len(design['definitions'])} definitions, "
          f"{len(design['instances']) - 1} child instances. Reports: {options.out}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filelist", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--out", "--work", type=Path, default=DEFAULT_REPORTS, dest="out",
                        help="Dedicated report directory (default: init_design/reports)")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG,
                        help="Text transcript (default: init_design/logs/init_design.log)")
    parser.add_argument("--interfaces", type=Path, action="append", default=[],
                        help="Optional JSON port interfaces for explicit cell or macro instances")
    parser.add_argument("--parameter", action="append", default=[], metavar="NAME=EXPR",
                        help="Override a top-level integral parameter")
    options = parser.parse_args(argv)
    try:
        if options.log.resolve().is_relative_to(options.out.resolve()):
            raise FlowError("The log path must be outside the report directory")
        options.log.parent.mkdir(parents=True, exist_ok=True)
        with options.log.open("w", encoding="utf-8", buffering=1) as stream:
            def log(stage, message, severity="info"):
                print(f"[{severity.upper()}] [{stage.upper()}] {message}", file=stream)

            try:
                return run(options, log)
            except (FlowError, OSError) as exc:
                log("run", f"Status: FAILED: {exc}", "error")
                raise
    except (FlowError, OSError) as exc:
        parser.exit(1, f"init_elab: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
