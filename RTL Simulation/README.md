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
4. Select a signal under **Trace signal** to highlight its referencing blocks.
   The inspector shows port directions, widths, parent bindings, and source
   locations. **Local RTL** shows assignments and processes in that scope.
5. Open **Generated file list** to inspect or download `sources.f`. Paths are
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
- The diagram groups references to the same signal symbol. Slices and
  expressions are visible in the port bindings. Highlighting is at signal
  level. It does not infer bit-level connectivity or trace through assignments.
- Quoted headers found by the compiler are listed in the UI. Discovery parses
  candidates independently, so project-specific defines, package imports driven
  by earlier file macros, and custom include paths can require a future
  project manifest. The current workbench searches the source file's directory,
  selected source directory, and RTL root for headers.
- This milestone ends at elaboration. Testbench execution, result assertions,
  and waveform viewing belong to the next milestone.
