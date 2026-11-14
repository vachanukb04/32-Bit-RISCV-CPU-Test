#!/usr/bin/env python3
"""Reproduce the VLSI Suite 101 RISC-V simulation and pre-layout STA baseline."""

import argparse
import hashlib
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "Synthesis/setup"
WRAPPER = REPO / "RiscV-32bit/RiscV-32bit.srcs/sources_1/new/nexys4_top.v"
TESTBENCH = WRAPPER.with_name("tb_riscv_core.v")
SDC = Path(__file__).with_name("riscv_core_10ns.sdc")
EXPECTED = "Time: 55000 | IO Output changed to: 00005555"


def rtl_sources():
    result = []
    for line in (SETUP / "riscv_core.filelist").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        prefix = "${REPO_ROOT}/"
        if not line.startswith(prefix):
            raise ValueError(f"Expected a repository-root RTL path: {line}")
        path = (REPO / line[len(prefix):]).resolve()
        if not path.is_relative_to(REPO) or not path.is_file():
            raise ValueError(f"Invalid RTL source: {path}")
        result.append(path)
    if not result:
        raise ValueError("The RISC-V file list is empty")
    return result


def run(command, out, name, *, cwd=None, append=False):
    log = out / name
    with log.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(map(str, command))}\n")
        stream.flush()
        proc = subprocess.run(command, cwd=cwd or out, stdout=stream, stderr=subprocess.STDOUT,
                              timeout=300, check=False)
    if proc.returncode:
        raise ValueError(f"{command[0]} exited with {proc.returncode}. See {log}")
    return log.read_text(encoding="utf-8", errors="replace")


def baseline(out, liberty, abc_constraints=None):
    for executable in ("iverilog", "vvp", "yosys", "sta"):
        if not shutil.which(executable):
            raise ValueError(f"Required tool missing from PATH: {executable}")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"Choose an empty output directory: {out}")
    if not liberty.is_file():
        raise ValueError(f"Liberty file not found: {liberty}")

    out.mkdir(parents=True, exist_ok=True)
    sources = rtl_sources()
    # Keep names fixed inside the tool scripts, regardless of checkout paths.
    rtl = out / "rtl"
    rtl.mkdir()
    for path in [*sources, WRAPPER, TESTBENCH]:
        shutil.copyfile(path, rtl / path.name)
    shutil.copyfile(liberty, out / "cells.lib")
    shutil.copyfile(SDC, out / SDC.name)
    abc_constraint_arg = ""
    if abc_constraints is not None:
        shutil.copyfile(abc_constraints, out / "abc.constr")
        abc_constraint_arg = " -constr abc.constr"
    (out / "inputs.txt").write_text(
        "RTL revision: " + subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        + "\nLiberty SHA256: " + hashlib.sha256((out / "cells.lib").read_bytes()).hexdigest()
        + "\n" + "\n".join(str(p.relative_to(REPO)) for p in sources) + "\n",
        encoding="utf-8",
    )
    # Match the compile order shown in the ChipVerify console.
    sim_sources = sorted(f"rtl/{path.name}" for path in [*sources, WRAPPER])
    run(["iverilog", "-g2012", "-s", "tb_riscv_core", "-o", "baseline_sim",
         *sim_sources, f"rtl/{TESTBENCH.name}"], out, "simulation.log")
    sim = run(["vvp", "baseline_sim", "+random_seed+1415069051"], out, "simulation.log", append=True)
    if EXPECTED not in sim or not (out / "riscv_core.vcd").is_file():
        raise ValueError(f"Expected RISC-V smoke test output missing. See {out / 'simulation.log'}")

    (out / "map.ys").write_text("\n".join([
        "read_verilog " + " ".join(f"rtl/{p.name}" for p in sources),
        "hierarchy -check -top riscv_core",
        "synth -top riscv_core -flatten -noabc",
        "dfflibmap -liberty cells.lib",
        "abc -liberty cells.lib -D 10000" + abc_constraint_arg,
        "clean",
        "read_liberty -lib cells.lib",
        "hierarchy -check -top riscv_core",
        "check -assert -mapped",
        "stat -liberty cells.lib",
        "write_verilog -noattr -noexpr -simple-lhs riscv_core_mapped.v",
        "",
    ]), encoding="utf-8")
    run(["yosys", "-s", "map.ys"], out, "synthesis.log")
    (out / "timing.tcl").write_text("\n".join([
        "read_liberty cells.lib",
        "read_verilog riscv_core_mapped.v",
        "link_design riscv_core",
        f"read_sdc {SDC.name}",
        "report_clock_properties [get_clocks *]",
        "check_setup -verbose",
        'puts "=== SETUP ==="',
        "report_checks -path_delay max -group_count 3 -fields {slew capacitance net fanout} -digits 3",
        "report_worst_slack -digits 3",
        "report_tns -digits 3",
        'puts "=== HOLD ==="',
        "report_checks -path_delay min -group_count 3 -fields {slew capacitance net fanout} -digits 3",
        "exit",
        "",
    ]), encoding="utf-8")
    sta = run(["sta", "-exit", "timing.tcl"], out, "timing.log")
    if re.search(r"^Error\b", sta, re.MULTILINE):
        raise ValueError(f"OpenSTA reported errors. See {out / 'timing.log'}")
    sections = sta.split("=== HOLD ===")
    setup = re.search(r"^worst slack\s+(\S+)", sections[0], re.MULTILINE)
    hold = re.search(r"^\s*([-+]?\d+\.\d+)\s+slack \(", sections[-1], re.MULTILINE)
    if len(sections) != 2 or not setup or not hold:
        raise ValueError(f"Missing constrained timing results. See {out / 'timing.log'}")
    print(f"Smoke test: PASS. Setup slack: {setup[1]} ns. Worst reported hold: {hold[1]} ns.")
    print(f"Reports and replay scripts: {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--liberty", required=True, type=Path,
                        help="Target Liberty file (ns timing units)")
    parser.add_argument("--abc-constraints", type=Path,
                        help="ABC input driver and output load file for buffering and sizing")
    parser.add_argument("--out", required=True, type=Path, help="New or empty run directory")
    args = parser.parse_args()
    try:
        baseline(args.out.resolve(), args.liberty.resolve(), args.abc_constraints)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Baseline failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
