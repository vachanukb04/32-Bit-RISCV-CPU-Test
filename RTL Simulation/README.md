# RTL2GDS Cockpit

The design workbench discovers Verilog and SystemVerilog sources and displays
the chosen top module as a hierarchy and connected schematic. It starts with
an empty project. The RISC-V example opens on demand.

## Run

From the repository root, with Python 3.11 or later:

```sh
python3 -m venv "RTL Simulation/.venv"
"RTL Simulation/.venv/bin/python" -m pip install -r "RTL Simulation/requirements.txt"
"RTL Simulation/.venv/bin/python" "RTL Simulation/serve.py"
```

Open the cockpit and select **Understanding your design**. [localhost:8080](http://127.0.0.1:8080)

Use **Connect GitHub** with a repository URL or `owner/repository`. Choose a
branch, tag, commit, or source folder when needed. Use the revision field for
branch names containing slashes. The imported files and their source links are
pinned to the resolved commit. **Refresh from GitHub** reads the current ref
and analyzes the previously selected top if it is still available. A private
repository requires a fine-grained token with read access to its contents.
The page holds the token in memory until the project closes or the page reloads.
Public imports use GitHub ZIP archives up to 32 MB and fall back to the GitHub
API when an archive is unavailable. ZIP imports preserve commit-pinned links.

**Create Project** starts a named project in the current browser tab. Use
**Add RTL files** or **Add RTL folder** to populate it. **Open RTL folder**
preserves file paths and includes. **Open RTL files** is
convenient for a source set within one directory. Both load sources into the
browser. The local Python service also offers **Open a directory on this machine**
for direct Slang elaboration of a working tree, where `.` means this checkout.

**Analyze RTL** uses Slang to generate a source-linked logical block diagram.
GitHub and browser-selected projects run the same analyzer in a WebAssembly worker.
The RISC-V example tracks the selected top, and the local Python service uses
native Slang for directories opened directly on the machine.

1. Load a project. Use **Source folder** in the GitHub dialog or
   **Source directory** in the local Slang flow to scope a project with multiple RTL trees.
2. Select a top and definition path. For the RISC-V core, select
   `riscv_core` from `RiscV-32bit/RiscV-32bit.srcs/sources_1/new/riscv_core.v`.
   For the simulation hierarchy, select `tb_riscv_core` from the same directory.
3. Click **Analyze RTL**. Click a block to enter it. The breadcrumbs
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
  The module schematic encloses the inferred `always @*` and `always_comb`
  logic in dotted boundaries. Input ports are on the left rail. Literal values,
  including initial writes, appear in orange at their consuming pins. Case arms
  appear in colored dotted regions with their RTL labels. Nested cases stay in
  their enclosing arm, with shared selection beside the regions. Arithmetic and
  comparisons use circular symbols. Clicking a wire or
  pin highlights the driver and all loads in the displayed scope. A wide signal uses one bus
  with its range. Concatenations join their fields with a brace label such as
  `{[31:16] [15:0]}`, in RTL order. Initialization
  shows known writes and statically resolved memory index sweeps. The process
  blocks link to the original RTL. Synthesis determines the final cells and
  memory mapping.
- The workbench uses the elaborated event and assignment semantics to draw
  D flip-flops and D latches. Clock-edge polarity and latch-enable polarity
  appear on their control pins. A synchronous reset selects the D input
  through a mux. A recognized asynchronous constant reset has its own
  reset pin. Complex processes outside the supported forms retain a link to
  their RTL. [Yosys process lowering](https://yosyshq.readthedocs.io/projects/yosys/en/stable/using_yosys/synthesis/proc.html)
- Quoted headers found by the compiler are listed in the UI. Discovery parses
  candidates independently, so project-specific defines, package imports driven
  by earlier file macros, and custom include paths can require a future
  project manifest. The current workbench searches the source file's directory,
  selected source directory, and RTL root for headers.

## Simulate your design

Open a project in **Understanding your design** and select its top module.
After reviewing the RTL, click **Reviewed & Proceed**. The simulation workflow
opens with the project's source files already loaded. For parallel RTL variants,
the handoff uses the selected source directory and any dependencies found by
Analyze RTL. A matching project testbench is selected when available. You can
also add simulation files or paste a testbench and set its top.
The browser worker uses
Icarus Verilog to compile and execute the testbench. Its console shows simulator
diagnostics and testbench output. A testbench that records VCD using
`$dumpfile` and `$dumpvars` produces an interactive waveform and a downloadable
VCD file. Returning to the workbench preserves the active project. Changing its
sources or top requires another review.
The worker fetches the pinned [Icarus Verilog WebAssembly port](https://github.com/senolgulgonul/verisim/tree/ab0ff3862a8cf8c6535117a64d370c4bf263042f)
on the first run.

## Rebuild the browser analyzer

The included browser analyzer pairs Slang 12.1.0 with Pyodide 0.27.7. When the
Python analysis modules change, run `package-browser-analysis.py` again with
the same extension and dependency notices. To rebuild the extension, use the
Slang 12.1.0 source distribution, Emscripten 3.1.58, the Pyodide 0.27.7 CPython
3.12.7 headers, and a host Python environment with nanobind 3.0.1. Run these
commands from the unpacked Slang source directory. Set `cockpit_dir` to this
`RTL Simulation` directory and `pyodide_headers` to the directory containing
Pyodide's `Python.h`.

```sh
patch -p1 < "$cockpit_dir/slang-wasm.patch"
emcmake cmake -S . -B browser-build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_SCAN_FOR_MODULES=OFF \
  -DCMAKE_CXX_FLAGS_RELEASE="-O1 -g0 -fexceptions -fPIC" \
  -DCMAKE_MODULE_LINKER_FLAGS="-sSIDE_MODULE=2 -sWASM_BIGINT -sEXPORTED_FUNCTIONS=_PyInit_pyslang -fexceptions" \
  -DPython_EXECUTABLE="$(command -v python)" -DPython_INCLUDE_DIR="$pyodide_headers" \
  -DSLANG_INCLUDE_PYLIB=ON -DSLANG_INCLUDE_TESTS=OFF -DSLANG_INCLUDE_TOOLS=OFF \
  -DSLANG_USE_THREADS=OFF -DSLANG_USE_MIMALLOC=OFF
cmake --build browser-build --target pyslang
python "$cockpit_dir/package-browser-analysis.py" \
  browser-build/lib/pyslang.cpython-312-wasm32-emscripten.so . \
  "$cockpit_dir/rtl-analysis.zip"
```

Pass the linked dependency notices as `--notice NAME PATH` arguments to
include them in the archive alongside the Slang license.
