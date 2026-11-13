# VLSI Suite 101

The goal is a reproducible RTL-to-layout flow with SDC and RTL checks tied to the elaborated design. The in-house init and elaboration engine already produces a source-located hierarchy, typed port bindings, diagnostics, and a design database. The reference baseline in this directory establishes expected simulation and pre-layout timing behavior for the same design.

| Stage | VLSI Suite 101 scope |
| --- | --- |
| Project and resources | Source browser, ordered file lists, explicit tops, compile options, cell and macro interfaces, and a run history with source and library identifiers |
| Simulation and debug | Console, deterministic test configuration, testbench results, VCD waveform viewing, and regression comparison |
| RTL lint and coverage | File and line diagnostics, test coverage summaries, and configurable project checks for widths, drivers, resets, clocks, and inferred storage |
| Formal | Explicit assertions and assumptions, proof bounds, and counterexample traces |
| Synthesis | Technology mapping, hierarchical and flattened netlists, area, and synthesis diagnostics |
| Timing and SDC checks | Clock reachability, generated clock validation, unmatched collections, unconstrained endpoints, exception coverage and precedence, MCMM timing, and checks across RTL and mapped netlists |
| Physical implementation | Floorplan, placement, CTS, routing, extraction, post-route timing, and physical artifacts |

This milestone includes the SDC and a reference runner. The remaining stages in the table define the suite's scope.

## Reproduce the reference run

From the repository root, with Icarus Verilog, Yosys/ABC, and OpenSTA on PATH:

```sh
python3 Synthesis/validation/run_riscv_baseline.py \
  --liberty /path/to/sky130_fd_sc_hd__tt_025C_1v80.lib \
  --out build/riscv_baseline
```

The external tools provide a comparison baseline for developing the in-house engine. Use the [Sky130 HD TT Liberty view](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib) at 1.80 V and 25 °C, with ns timing units. The runner records its hash and generates replay scripts, simulator and synthesis logs, a VCD, a mapped netlist, and the timing report. The simulation top is `tb_riscv_core`. Synthesis and STA use `riscv_core` without the FPGA wrapper or testbench.

The [baseline SDC](riscv_core_10ns.sdc) specifies an ideal 10 ns clock on `clk`. It leaves boundary delays unspecified so the STA coverage report exposes missing constraints. The run does pre-layout setup and hold analysis without SPEF. Negative slack is a valid baseline result and indicates a timing violation for the modeled path. Reset timing, I/O budgets, physical constraints, and extracted parasitics are needed for a complete timing assessment.
