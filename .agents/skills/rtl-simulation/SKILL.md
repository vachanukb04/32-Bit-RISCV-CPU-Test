---
name: rtl-simulation
description: Build and verify RTL2GDS Cockpit testbench runs, simulator diagnostics, and source-generated waveforms.
---

- Treat the chosen testbench top as the root of the simulation. Keep source and include paths through preprocessing and show real Icarus Verilog diagnostics and testbench output.
- Run preprocessing, compilation, and simulation in a dedicated browser worker. Return output from the actual testbench. End a run that exceeds the worker deadline.
- Use the VCD timescale, scope-qualified signal names, and value changes recorded by the simulator. Preserve unknown and high-impedance values and show the value at the selected time.
- Keep simulation in the Simulate your design workflow. The design-understanding workflow uses Slang analysis to build its logical block diagram.
- Verify with the built-in flip-flop example and an independent testbench. Check edge-triggered updates, a known output value, the VCD download, and diagnostics for an invalid design.

The workflow lives in `RTL Simulation/simulate.html`, `simulate.js`, `rtl-sim-worker.js`, and `rtl-vcd.js`. The Icarus WebAssembly toolchain is loaded from the pinned [VeriSim port](https://github.com/senolgulgonul/verisim/tree/ab0ff3862a8cf8c6535117a64d370c4bf263042f).
