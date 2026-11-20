---
name: rtl-simulation
description: Prepare, run, and verify Verilog/SystemVerilog simulations with the RTL2GDS Cockpit engine.
---

- Use the exact source revision or reviewed file set. Preserve file order and paths and record the testbench top, include paths, defines, time limit, and requested trace signals. The testbench top is the elaboration root. When drafting a testbench, derive ports from RTL and use user-provided stimulus and expected behavior for self-checks.
- Run Analyze, Elaborate, and Simulate through the browser worker or the equivalent CLI in `RTL Simulation/rtl_sim_cli.py`. Slang handles language analysis and hierarchy. The cockpit runtime handles signal values, scheduling, and output.
- Preserve four-state values, same-time event regions, clock edge detection, and the nonblocking update of a captured value and destination. Surface unsupported constructs with their source locations. Stop a run that exceeds the worker or event budget.
- Use the VCD timescale, scope-qualified signal names, and value changes recorded by the simulator. Preserve unknown and high-impedance values and show the value at the selected time.
- Keep simulation in the Simulate your design workflow. The design-understanding workflow uses Slang analysis to build its logical block diagram.
- Unlock simulation from Reviewed & Proceed with the active project and top. Keep both views in the same tab, preserve file paths and reviewed sources, and invalidate the review when the project or top changes.
- Verify with the bundled RISC-V project and independent self-checking testbenches for muxes, clocked registers, and memory. Compare waveform transitions and testbench results with Icarus during validation. Check the workflow gate, source handoff, and VCD download. Record the stop reason separately from explicit test assertions.

The engine and its feature scope live under `RTL Simulation`. The GUI uses `simulate.html`, `simulate.js`, and `rtl-sim-worker.js`.
