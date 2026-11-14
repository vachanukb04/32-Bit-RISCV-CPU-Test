#!/usr/bin/env python3
"""Run bounded safety and cover checks on the RISC-V boot program."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILELIST = ROOT / "Synthesis/setup/riscv_core.filelist"
TOP = "riscv_core_formal_top"
SOLVERS = ("bitwuzla", "z3", "boolector")


def run(command, output_dir, log_name, timeout):
    log_path = output_dir / log_name
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                              cwd=output_dir, start_new_session=True) as process:
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                log.write(f"\nRunner timeout after {timeout}s\n")
                return_code = None
    return return_code, round(time.monotonic() - start, 3)


def classify(log_path, return_code, mode):
    if return_code is None:
        return "TIMEOUT"
    output = log_path.read_text(encoding="utf-8", errors="replace")
    statuses = re.findall(r"Status:\s+(\w+)", output)
    if statuses and return_code == 0 and statuses[-1] == "PASSED":
        return "PASS"
    if statuses and statuses[-1] == "PREUNSAT":
        return "INCONSISTENT_ASSUMPTIONS"
    if statuses and statuses[-1] == "FAILED" and return_code == 1:
        if mode == "bmc" and "Assert failed" in output:
            return "COUNTEREXAMPLE"
        if mode == "cover" and "Unreached cover statement" in output:
            return "COVER_UNREACHED"
    return "ERROR"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="New directory for logs and traces")
    parser.add_argument("--depth", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120, help="Seconds per build or check")
    parser.add_argument("--memory-model", choices=("mapped", "arrays"), default="mapped",
                        help="Map memories to registers (default) or preserve SMT arrays")
    parser.add_argument("--solvers", choices=SOLVERS, nargs="+", default=SOLVERS)
    args = parser.parse_args()
    if args.depth < 1 or args.timeout < 1:
        parser.error("depth and timeout must be positive")
    for tool in ("yosys", "yosys-smtbmc", *args.solvers):
        if shutil.which(tool) is None:
            parser.error(f"Required executable is missing from PATH: {tool}")

    source_paths = []
    for line in FILELIST.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            path = Path(entry.replace("${REPO_ROOT}", str(ROOT)))
            if not path.is_file():
                parser.error(f"RTL source is missing: {path}")
            source_paths.append(path)
    if not source_paths:
        parser.error(f"No RTL sources found in {FILELIST}")
    source_paths.append(ROOT / "Formal/formal_props.sv")

    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    # Stage the listed inputs under their basenames to keep the Yosys script portable.
    source_names = [path.name for path in source_paths]
    if len(set(source_names)) != len(source_names):
        parser.error("RTL source basenames must be unique")
    (out / "src").mkdir()
    for path in source_paths:
        shutil.copyfile(path, out / "src" / path.name)
    script = "\n".join(f'read_verilog -formal -sv "src/{name}"' for name in source_names)
    script += f"\nprep -top {TOP} -flatten\nasync2sync\n"
    if args.memory_model == "mapped":
        script += "memory_map\nopt -full\ndffunmap\n"
    script += "write_smt2 -wires design.smt2\n"
    (out / "build.ys").write_text(script, encoding="utf-8")
    build_rc, build_time = run(["yosys", "-s", "build.ys"], out, "yosys.log", args.timeout)
    summary = {"top": TOP, "depth": args.depth, "timeout_seconds": args.timeout,
               "memory_model": args.memory_model, "build_seconds": build_time,
               "build_return_code": build_rc, "checks": []}

    if build_rc == 0:
        model = (out / "design.smt2").read_text(encoding="utf-8")
        counts = {kind: len(re.findall(rf"^; yosys-smt2-{kind} ", model, re.MULTILINE))
                  for kind in ("assert", "assume", "cover")}
        summary["properties"] = counts
        if not all(counts.values()):
            message = f"Formal model is missing a property type: {counts}"
            summary["build_error"] = message
            print(message, file=sys.stderr)

    if build_rc == 0 and "build_error" not in summary:
        for solver in args.solvers:
            for mode in ("bmc", "cover"):
                log_name = f"{solver}_{mode}.log"
                trace_name = f"{solver}_{mode}" + ("_%.vcd" if mode == "cover" else ".vcd")
                command = ["yosys-smtbmc", "-s", solver, "-t", str(args.depth),
                           "--presat", "--noprogress", "--dump-vcd", trace_name]
                if mode == "cover":
                    command.append("-c")
                command.append("design.smt2")
                return_code, elapsed = run(command, out, log_name, args.timeout)
                status = classify(out / log_name, return_code, mode)
                summary["checks"].append({"solver": solver, "mode": mode, "status": status,
                                          "return_code": return_code, "seconds": elapsed,
                                          "log": log_name})
                print(f"{solver:9} {mode:5} {status:24} {elapsed:.3f}s", flush=True)
    elif build_rc != 0:
        print(f"Model build failed. See {out / 'yosys.log'}", file=sys.stderr)

    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    checks_passed = all(check["status"] == "PASS" for check in summary["checks"])
    return int(build_rc != 0 or "build_error" in summary or not checks_passed)


if __name__ == "__main__":
    sys.exit(main())
