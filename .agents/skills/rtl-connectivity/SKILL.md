---
name: rtl-connectivity
description: Build and verify source-linked RTL Workbench schematics showing combinational process boundaries, value sources, storage, and full signal connections.
---

- Read the elaborated Slang AST. Follow procedural assignments in execution order and preserve the last blocking assignment on each path. Changes to inputs read by an `always @*` block re-evaluate that block. Use clock events and hold paths to identify flip-flops and latches.
- Draw a dotted region for each implicit-sensitivity combinational process. Route data and selector inputs into the inferred logic. Route live values to the output and local signal nodes. Keep reset-path values and procedural defaults tied to the mux pins that actually use them.
- Place input ports on the left and output ports on the right. Draw constant values in orange with a short stub at the consuming pin. Preserve literal widths and distinct values for power-up writes, procedural defaults, and case comparisons.
- Draw arithmetic and comparisons as circles with their operator marks. Keep standard gate shapes, a mux wedge, and sequential cell symbols. Preserve the exact `==` or `===` distinction and render concatenations in RTL bit order.
- Trace from a clicked wire or pin to its driver, junctions, and loads within the module. Follow direct connections and named nets. Stop at gates that compute a new value.

Use `RTL Simulation/compiler.py` and `rtl_comb_logic.py` for circuit inference. The graph, layout, and SVG rendering live in `schematic-graph.js`, `schematic-layout.js`, and `schematic.js`. Run the compiler and schematic suites, then check `riscv_core → u_dec` in the browser. Confirm that the reset path, instruction slices, immediate muxes, and constants cross the correct boundary and trace to the expected pins.
