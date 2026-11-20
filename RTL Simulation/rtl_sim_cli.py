"""Analyze, elaborate, and run Verilog/SystemVerilog with the cockpit simulator."""

import argparse
from pathlib import Path
import re
import sys

import pyslang
from pyslang import ast, parsing, syntax

from rtl_sim_engine import Engine, SimulationError, TIME_UNITS
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


def clocks_for(top):
    clocks = {}

    def visit(node):
        if isinstance(node, ast.InstanceSymbol):
            scale = node.body.timeScale
            clocks[node.hierarchicalPath] = (Clock(quantity(scale.base), quantity(scale.precision))
                                            if scale else DEFAULT_CLOCK)
    top.visit(visit)
    return clocks


def run_project(root, files, top, *, includes=(), defines=(), limit=None, trace=(), report=lambda stage: None):
    compiler_log = ""
    engine = None
    phase = "Analyze"
    try:
        root = Path(root).resolve()
        report(phase)
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
        prep.additionalIncludePaths = list(dict.fromkeys([
            *(project_path(root, folder) for folder in includes), root,
            *(path.parent for path in paths)
        ]))
        prep.predefines = list(defines)
        settings = ast.CompilationOptions()
        settings.topModules = {top}
        settings.defaultTimeScale = pyslang.TimeScale.fromString("1ns/1ps")
        options = pyslang.Bag()
        options.preprocessorOptions = prep
        options.compilationOptions = settings
        manager = pyslang.SourceManager()
        tree = syntax.SyntaxTree.fromFiles(list(map(str, paths)), manager, options)
        parse_diagnostics = tree.diagnostics
        if any(diag.isError() for diag in parse_diagnostics):
            compiler_log = pyslang.DiagnosticEngine.reportAll(manager, parse_diagnostics).replace(str(root) + "/", "")
            raise SimulationError("Source analysis failed.")

        phase = "Elaborate"
        report(phase)
        compilation = ast.Compilation(options)
        compilation.addSyntaxTree(tree)
        instances = compilation.getRoot().topInstances
        diagnostics = compilation.getAllDiagnostics()
        compiler_log = pyslang.DiagnosticEngine.reportAll(manager, diagnostics).replace(str(root) + "/", "")
        if any(diag.isError() for diag in diagnostics):
            raise SimulationError("Could not elaborate the testbench.")
        if len(instances) != 1 or instances[0].name != top:
            raise SimulationError(f"Testbench top {top} did not resolve to one module instance.")
        clocks = clocks_for(instances[0])
        tick = min(clock.precision for clock in clocks.values())
        engine = Engine(tick, None if limit is None else limit * tick)
        model = Model(engine, manager, root, clocks)
        model.start(instances[0])
        model.capture(trace)

        phase = "Simulate"
        report(phase)
        result = engine.run()
        result["log"] = "\n".join(filter(None, (compiler_log.rstrip(), result["log"])))
        result["engine"] = "RTL2GDS Simulator"
        result["testErrors"] = engine.test_errors
        return {"result": result}
    except (SimulationError, OSError) as error:
        output = "\n".join(filter(None, (compiler_log.rstrip(), "\n".join(engine.log) if engine else "")))
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
