"""Analyze, elaborate, and run Verilog/SystemVerilog with the cockpit simulator."""

import argparse
from pathlib import Path
import re
import sys

import pyslang
from pyslang import ast, parsing, syntax

from rtl_sim_engine import Engine, SimulationError, TIME_UNITS, timescale
from rtl_sim_model import Clock, Model


SOURCE = re.compile(r"\.(v|sv)$", re.IGNORECASE)
DEFAULT_CLOCK = Clock(TIME_UNITS["ns"], TIME_UNITS["ps"])


def quantity(value):
    match = re.fullmatch(r"(1|10|100)\s*(s|ms|us|ns|ps|fs)", str(value))
    if match is None:
        raise SimulationError(f"Unsupported time scale {value}.")
    count, unit = match.groups()
    return int(count) * TIME_UNITS[unit]


def project_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise SimulationError(f"Path must be inside the project: {name}")
    return path


def describe_instances(top):
    clocks = {}
    instances = []

    def visit(node):
        if isinstance(node, ast.InstanceSymbol):
            scale = node.body.timeScale
            clocks[node.hierarchicalPath] = (Clock(quantity(scale.base), quantity(scale.precision))
                                            if scale else DEFAULT_CLOCK)
            instances.append(f"{node.hierarchicalPath} ({node.definition.name})")
    top.visit(visit)
    return clocks, instances


def list_entries(items, *, max_rows=200):
    tail = [f"  … {len(items) - max_rows} more"] if len(items) > max_rows else []
    return "\n".join([*(f"  {item}" for item in items[:max_rows]), *tail]) or "  None"


def run_project(root, files, top, *, includes=(), defines=(), limit=None, trace=(),
                report=lambda stage: None, progress=lambda stage, action, detail: None):
    compiler_log = ""
    engine = None
    phase = "Analyze"

    def record(action, detail=""):
        progress(phase, action, detail)

    def diagnostics_text(diagnostics):
        return pyslang.DiagnosticEngine.reportAll(manager, diagnostics).replace(str(root) + "/", "").rstrip()

    try:
        root = Path(root).resolve()
        report(phase)
        record("Validate the run configuration", f"Requested testbench top · {top}")
        if not re.fullmatch(r"[A-Za-z_][\w$]*", top):
            raise SimulationError("Enter a valid testbench top module.")
        paths = [project_path(root, name) for name in files if SOURCE.search(name)]
        if not paths:
            raise SimulationError("Add Verilog or SystemVerilog sources.")
        if any(not path.is_file() for path in paths):
            raise SimulationError("A configured RTL source file is missing.")
        if limit is not None and (type(limit) is not int or not 0 <= limit <= 2**53 - 1):
            raise SimulationError("The time limit must be a nonnegative integer in simulation ticks.")

        prep = parsing.PreprocessorOptions()
        include_paths = list(dict.fromkeys([
            *(project_path(root, folder) for folder in includes), root,
            *(path.parent for path in paths)
        ]))
        prep.additionalIncludePaths = include_paths
        prep.predefines = list(defines)
        settings = ast.CompilationOptions()
        settings.topModules = {top}
        settings.defaultTimeScale = pyslang.TimeScale.fromString("1ns/1ps")
        options = pyslang.Bag()
        options.preprocessorOptions = prep
        options.compilationOptions = settings
        manager = pyslang.SourceManager()
        record(f"Preprocess and parse {len(paths)} source files with Slang",
               f"Source files in input order\n{list_entries([path.relative_to(root) for path in paths])}\n\n"
               f"Include search directories\n{list_entries([path.relative_to(root) for path in include_paths])}\n\n"
               f"Preprocessor defines\n{list_entries(list(prep.predefines))}")
        tree = syntax.SyntaxTree.fromFiles(list(map(str, paths)), manager, options)
        parse_diagnostics = tree.diagnostics
        compiler_log = diagnostics_text(parse_diagnostics)
        if compiler_log:
            record("Read Slang parsing diagnostics", compiler_log)
        if any(diag.isError() for diag in parse_diagnostics):
            raise SimulationError("Source analysis failed.")
        headers = sorted({str(manager.getFullPath(include.buffer.id)).removeprefix(str(root) + "/")
                          for include in tree.getIncludeDirectives() if include.buffer})
        record("Parsed the source files", "Resolved include files\n" + list_entries(headers))

        phase = "Elaborate"
        report(phase)
        record("Resolve the testbench top, parameters, and port connections", f"Testbench top · {top}")
        compilation = ast.Compilation(options)
        compilation.addSyntaxTree(tree)
        instances = compilation.getRoot().topInstances
        diagnostics = compilation.getAllDiagnostics()
        compiler_log = diagnostics_text(diagnostics)
        semantic_log = diagnostics_text(compilation.getSemanticDiagnostics())
        if semantic_log:
            record("Read Slang elaboration diagnostics", semantic_log)
        if any(diag.isError() for diag in diagnostics):
            raise SimulationError("Could not elaborate the testbench.")
        if len(instances) != 1 or instances[0].name != top:
            raise SimulationError(f"Testbench top {top} did not resolve to one module instance.")
        clocks, hierarchy = describe_instances(instances[0])
        record(f"Resolved {len(hierarchy)} module instances", "Instance paths and module names\n" + list_entries(hierarchy))
        tick = min(clock.precision for clock in clocks.values())
        engine = Engine(tick, None if limit is None else limit * tick)
        model = Model(engine, manager, root, clocks)
        record("Build the runnable model and connect signal drivers")
        model.start(instances[0])
        model.capture(trace)
        record("Prepared the model for simulation",
               f"Processes · {len(model.processes)}\n"
               f"Continuous drivers and port connections · {len(model.drivers)}\n"
               f"Signals and memories in module scopes · {len(model.visible)}")

        phase = "Simulate"
        report(phase)
        record("Configure the event scheduler and waveform capture",
               f"Simulation tick · {timescale(tick)}\n"
               f"Time limit · {str(limit) + ' ticks' if limit is not None else 'None'}\n\n"
               f"Signals or scopes requested in Run settings\n{list_entries(list(trace))}\n\n"
               "Testbench $dumpvars requests are applied when they execute.")
        record("Run RTL and testbench events",
               "Advance to the next scheduled time and process Active, Inactive, and nonblocking assignment events.")
        result = engine.run()
        if result["log"]:
            record("Testbench output", result["log"])
        wave = result["wave"]
        waveform = f"{wave['name']} ({len(engine.wave.ids)} signals)" if wave else "None"
        record(f"Stopped at {result['ticks']} × {result['timescale']}",
               f"Stop reason · {result['reason']}\n"
               f"Scheduler and statement steps · {result['events']}\n"
               f"Testbench errors · {engine.test_errors}\n"
               f"Waveform · {waveform}")
        result["log"] = "\n".join(filter(None, (compiler_log.rstrip(), result["log"])))
        result["engine"] = "RTL2GDS Simulator"
        result["testErrors"] = engine.test_errors
        return {"result": result}
    except (SimulationError, OSError) as error:
        test_output = "\n".join(engine.log) if engine else ""
        if test_output and phase == "Simulate":
            record("Testbench output before the error", test_output)
        record("Stage failed", str(error))
        output = "\n".join(filter(None, (compiler_log.rstrip(), test_output)))
        return {"phase": phase, "error": str(error), "log": output}


def main():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("sources", nargs="+")
    command.add_argument("--top", required=True, help="Testbench top module")
    command.add_argument("--root", default=".", help="Project root. Source and include paths are relative to it.")
    command.add_argument("-I", "--include", action="append", default=[])
    command.add_argument("-D", "--define", action="append", default=[])
    command.add_argument("--limit", type=int, help="Time limit in simulation ticks")
    command.add_argument("--trace", action="append", default=[], help="Hierarchical signal or instance to capture")
    command.add_argument("--vcd", type=Path, help="Destination for the waveform")
    args = command.parse_args()
    response = run_project(args.root, args.sources, args.top, includes=args.include, defines=args.define,
                           limit=args.limit, trace=args.trace, report=lambda stage: print(stage, file=sys.stderr))
    if "error" in response:
        print(response["log"], file=sys.stderr)
        print(f"{response['phase']}: {response['error']}", file=sys.stderr)
        return 1
    result = response["result"]
    print(result["log"])
    print(f"{result['reason']} at {result['ticks']} × {result['timescale']}.")
    if args.vcd and result["wave"]:
        args.vcd.write_text(result["wave"]["text"])
    return int(result["testErrors"] > 0)


if __name__ == "__main__":
    sys.exit(main())
