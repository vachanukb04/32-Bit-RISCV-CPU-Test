---
name: rtl-connectivity
description: Build and verify source-linked RTL2GDS Cockpit schematics showing combinational process boundaries, value sources, storage, and full signal connections.
---

- Read the elaborated Slang AST. Follow procedural assignments in execution order and preserve the last blocking assignment on each path. Changes to inputs read by an `always @*` block re-evaluate that block. Use clock events and hold paths to identify flip-flops and latches.
- Generate the logical diagram from the same Slang analysis for GitHub sources, browser files, local folders, and example tops. Preserve the exact top definition path and source revision so the source viewer and schematic refer to the same RTL.
- Draw a dotted region for each implicit-sensitivity combinational process. Route data and selector inputs into the inferred logic. Route live values to the output and local signal nodes. Keep reset-path values and procedural defaults tied to the mux pins that actually use them.
- Read case selectors, arms, and nesting from the elaborated AST and their source ranges. Give each written arm a colored dotted outline labeled with its RTL match values or `default`. Keep a nested case within its enclosing arm. Keep the match logic and assigned values inside the corresponding arm and put shared selection beside the arms. Preserve grouped labels, elaborated parameters, wildcard and priority semantics, and explicit empty arms. Keep source links for case forms whose circuit cannot be inferred.
- Place input ports on the left and output ports on the right. Draw constant values in orange with a short stub at the consuming pin. Preserve literal widths and distinct values for power-up writes, procedural defaults, and case comparisons.
- Draw arithmetic and comparisons as circles with their operator marks. Keep standard gate shapes, a mux wedge, and sequential cell symbols. Preserve the exact `==` or `===` distinction and render concatenations in RTL bit order.
- Trace from a clicked wire or pin to its driver, junctions, and loads within the module. Follow direct connections and named nets. Stop at gates that compute a new value.

Use `RTL Simulation/compiler.py` and `rtl_comb_logic.py` for circuit inference and `rtl_case_regions.py` for case ownership. The graph, layout, and SVG rendering live in `schematic-graph.js`, `schematic-layout.js`, and `schematic.js`. Run the compiler and schematic suites with a generic nested-case fixture, then check the case grouping and wire tracing in an analyzed design. Use `riscv_core → u_ctrl` to verify case grouping and compare `riscv_core → u_rf` between the bundled and GitHub versions at the same revision to verify the register-bank view.
