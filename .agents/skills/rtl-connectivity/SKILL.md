---
name: rtl-connectivity
description: Explain elaborated Verilog and SystemVerilog in the RTL Workbench by following procedural values from input conditions through assignments to outputs. Use for changes to control decoders, combinational schematics, and signal tracing.
---

## Build the view from RTL

- Locate the module instance and the exact source path selected by Slang. Use elaborated widths, constants, and signal identities.
- For `always @*` and `always_comb`, follow blocking assignments in execution order. Start with the values assigned before branching. Walk the guard and the outer case in source order. Carry earlier values forward into any arm that leaves an output alone. For guards and nested branches, derive output values from the actual decision tree.
- Show a decoder as one row per outer case arm when it selects among constant control outputs. Show all final outputs on that row, including inherited defaults, and put a separate reset or bypass row above it when applicable. Keep nested cases and ternary checks under the output they refine. Display the original selector, resolved case codes, and links to RTL.
- A change to a read input re-evaluates an implicit-sensitivity combinational block. A procedural path that retains state needs a latch or an explicit hold path. Clock-edge events drive flip-flop clock pins. For datapaths with variable values, preserve gate and bus connectivity. Keep the detailed wiring available for a decoder.

## Verify

The decoder projection is in `RTL Simulation/rtl_comb_logic.py`. The browser renderer is in `RTL Simulation/schematic.js`. Run the compiler and schematic suites from `RTL Simulation`, then check the real `u_ctrl` instance of `riscv_core`. Confirm the reset result, each outer opcode result, the nested R-type and I-type selections, and the default. Compare the displayed values to `control_unit.v`. Verify that tracing an input highlights its decisions and tracing an output highlights its column. The hosted build's precompiled designs must come from the same compiler revision.
