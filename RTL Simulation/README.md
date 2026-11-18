# RTL Workbench

The first RTL Simulation milestone discovers Verilog and SystemVerilog sources,
compiles and elaborates a selected top with Slang, and displays the resulting
design in a browser. The Python service runs on the same machine as the browser.

## Run

From the repository root, with Python 3.11 or later:

```sh
python3 -m venv "RTL Simulation/.venv"
"RTL Simulation/.venv/bin/python" -m pip install -r "RTL Simulation/requirements.txt"
"RTL Simulation/.venv/bin/python" "RTL Simulation/serve.py"
```

Open [localhost:8080](http://127.0.0.1:8080). For **RTL root directory**, enter
a local directory, leave `.` for the current repository checkout, or paste a
public GitHub repository URL such as
[32-Bit-RISCV-CPU-Test](https://github.com/vachanukb04/32-Bit-RISCV-CPU-Test/tree/main).
GitHub input requires Git. A repository URL uses its default branch and a
`/tree/<branch>` URL selects a simple branch or tag name.

1. Click **Scan sources**. Use **Source directory** to scope the scan to a
   subfolder of the project when it contains multiple RTL trees.
2. Select a top and definition path. For the current RISC-V core, select
   `riscv_core` from `RiscV-32bit/RiscV-32bit.srcs/sources_1/new/riscv_core.v`.
   For the simulation hierarchy, select `tb_riscv_core` from the same directory.
3. Click **Compile & elaborate**. Click a block to enter it. The breadcrumbs
   return to any parent, and the tree supports independent expand and collapse.
4. Open **Data schematic** to see one wired canvas for the selected instance.
   Module ports, child pins, gates, memory, and clock fanout appear on the same
   canvas. Module inputs and bidirectional ports sit on the left edge, and
   outputs sit on the right. Click a wire, click a signal label, or select a
   net from **Trace signal** to highlight its driver and endpoints. Use **Fit** for an overview and **100%** to read
   pin names on a larger schematic.
5. Click a file location under **Compiler diagnostics** to open the source
   read-only at the highlighted line and column.
6. Open **Generated file list** to inspect or download `sources.f`. Paths are
   relative to the selected RTL root, so use that directory when replaying it.
   Pass the listed include directories to your compiler as include options.
   For Icarus, use `-grelative-include` to search beside the including file.

For the core, the expected hierarchy contains seven direct child instances.
The testbench top adds one level above the core and one source to the file list.
These are regression expectations. Top selection, file ordering, and diagram data
all come from the project sources.

## How it works

- Slang parses `.v` and `.sv` candidates. The workbench follows instantiated
  module types and package references, then orders source files with dependency
  files first. When definitions repeat, it prefers the definition beside the
  referring file. An unresolved choice reports all candidates so you can scope
  the source directory.
- The selected sources are compiled as one compilation unit. The compiler
  resolves the top, parameters, generate scopes, arrays, and port bindings,
  and supplies diagnostics. The browser uses the actual elaborated instances.
- The schematic joins expressions and port bindings by elaborated symbol in
  the current scope. Slices have labeled taps, wires branch at marked junctions,
  and child blocks open their own schematics. Tracing works at signal granularity.
- Continuous expressions and supported single-destination processes expand
  into circuit symbols. Concatenations show destination bit ranges and their
  operands in MSB-to-LSB order. Supported combinational processes show their
  external data and control inputs, guards, selectors, and driven outputs.
  Click the block to inspect each output's rules. **Show gates** draws the
  selected rule's RTL data path and branch enable separately, using logic
  symbols, case comparators, and prior-arm exclusions where required. **Show
  output circuit** draws the full decision and data network for that output. Initialization
  shows known writes and statically resolved memory index sweeps. The process
  blocks link to the original RTL. Synthesis determines the final cells and
  memory mapping.
- Quoted headers found by the compiler are listed in the UI. Discovery parses
  candidates independently, so project-specific defines, package imports driven
  by earlier file macros, and custom include paths can require a future
  project manifest. The current workbench searches the source file's directory,
  selected source directory, and RTL root for headers.
- This milestone ends at elaboration. Testbench execution, result assertions,
  and waveform viewing belong to the next milestone.
