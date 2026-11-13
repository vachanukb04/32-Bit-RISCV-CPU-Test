# VLSI Suite 101

This directory is for the in-house synthesis tool for the 32-bit RISC-V design. Phase 1 will turn the original Verilog sources into a linked RTL design database, starting at `riscv_core`. The database will describe the hierarchy, resolved parameters, typed signals and expressions, and port connections that later synthesis stages will consume.

The [RISC-V reference baseline](validation/README.md) documents the suite scope and reproduces simulation, synthesis, and a provisional 10 ns timing run.

## Phase 1 flow

| Stage | Algorithm | Output |
| --- | --- | --- |
| `init` | Read the project configuration and expand the source file list in order. Resolve paths, collect include directories and defines, validate the input files, and record file hashes, the selected top, and optional parameter overrides. | Input manifest and work directory |
| `analyze` | Process HDL includes, macro definitions and expansions, and conditional directives in one ordered compilation unit. Tokenize the result and parse it with a recursive descent parser and an operator precedence parser for expressions. Store each module as a reusable syntax tree with its ordered port declarations, parameters, signals, procedural blocks, and instance declarations. Keep the original source locations, including locations in included headers. Detect duplicate module definitions before linking. | Module index and parsed RTL |
| `elab` | Starting at the selected top, evaluate parameters, create instance scopes, expand supported generate constructs, and bind child ports to expressions or nets in their parent scopes. Resolve identifier references, packed widths, signedness, and unpacked array dimensions. Record any required conversions and diagnose unresolved references. | Elaborated hierarchy and diagnostics |

Run the flow from the repository root with Python 3.10 or newer:

```sh
python3 Synthesis/init_design/init_elab.py \
  --filelist Synthesis/setup/riscv_core.filelist \
  --top riscv_core
```

The command runs all three stages and writes `Synthesis/init_design/logs/init_design.log` and the reports under `Synthesis/init_design/reports/`. The transcript records ordered file reads and source hashes, preprocessing and tokenization, front-end module compilation, hierarchy linking, port checks, and warnings or errors. Front-end compilation produces parsed RTL for elaboration. Technology mapping occurs later.

Use `--out` to choose another dedicated report directory and `--log` to choose another transcript path. A rerun replaces the previous transcript and generated reports. A failed run records the phase and diagnostic without publishing a successful elaboration report.

| Output | Contents |
| --- | --- |
| `init.json` | Resolved input manifest, top module, defines, includes, source hashes, and optional interface catalogs |
| `analysis.json` | Parsed module definitions, procedural syntax, include dependencies, and source locations |
| `elaboration.json` | Resolved hierarchy with instance paths and port bindings, plus typed RTL, parameters, signal widths and signedness, array dimensions, and source locations for each definition |
| `elaboration.rpt` | Human-readable hierarchical report and port connections |
| `blocks/*.rpt` | Flat report for each resolved block definition, parameter specialization, or macro interface, including all instance paths that use it |
| `diagnostics.json` | Run status, stage, first fatal error or reported warnings, and available source and instance locations |

The design database preserves hierarchy. Flat block reports are local views of each definition and keep references to child instances. Multiple instances with the same effective parameters share a definition.

Init reports missing sources and invalid or recursive file lists. Analyze reports missing headers, undefined macros, syntax errors, unsupported syntax, and duplicate module definitions. Elaborate reports unresolved modules or macros, invalid or cyclic parameters, undeclared identifiers, recursive hierarchy, and invalid, duplicate, or wrong-width port bindings. Open input and bidirectional ports produce warnings. Intentional open outputs are recorded in the hierarchy.

## RTL file list

Use an ordered text manifest named `<design>.filelist` with one source or directive per line. During `init`, the tool expands the literal `${REPO_ROOT}` prefix to the absolute path of the Git checkout containing the top-level file list. Absolute paths can also be supplied directly. Other relative paths resolve from the directory containing the manifest that declares them. The reader accepts `#` and `//` comment lines, quoted paths, `+incdir+<directory>`, `+define+<name>[=<value>]`, and `-f <file>` for nested lists. It expands nested lists in place and reports recursive list references. HDL includes resolve relative to the including source first, then through the declared include directories in order.

The [RISC-V example](setup/riscv_core.filelist) contains:

```text
# Design sources for the riscv_core top
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/alu.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/control_unit.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/data_mem.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/inst_mem.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/instr_decode.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/pc.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/reg_file.v
${REPO_ROOT}/RiscV-32bit/RiscV-32bit.srcs/sources_1/new/riscv_core.v
```

Select the top explicitly at init. Keep board wrappers, testbenches, and FPGA constraints in their own project inputs. Module definition order is flexible because analysis indexes all definitions before elaboration. Source order still matters for preprocessor directives. Headers referenced by HDL includes are loaded through those directives.

See [setup](setup/README.md) for the functional cell library, explicit cell interfaces, and voltage and temperature configuration.

## Elaboration and hierarchy

1. **Specialize the top.** Look up the requested module, apply explicit top-level parameter overrides, and evaluate remaining parameter defaults and local parameters in dependency order. Diagnose invalid overrides and cyclic constant dependencies.
2. **Build the local scope.** Resolve port and signal types, packed ranges, and unpacked array dimensions. Expand supported generate conditions and loops using constant values. Preserve scope names and source locations so a diagnostic can identify both the instance path and RTL line.
3. **Link child instances.** Resolve each child against the module index or an explicitly registered macro interface. Evaluate child parameter override expressions in the parent scope, then specialize the child with those values. Reuse a specialization when the same module and effective parameter values recur, while giving each instance its own hierarchical path and connectivity.
4. **Bind and check ports.** Match named connections by name and positional connections by declaration order. Resolve the actual expression in the parent scope and the formal port in the child scope. Check directions, legal connection targets, unknown or duplicate bindings, and width differences. Record explicit open ports and supported sizing conversions. Report width differences and make them errors in the strict mode used for the RISC-V milestone.
5. **Type expressions and preserve behavior.** Apply Verilog sizing and signedness rules to constants, arithmetic, comparisons, concatenations, and selects, including cases where the destination affects expression sizing. Keep continuous assignments and procedural statements with their original ordering, event controls, and blocking or nonblocking assignment semantics. Preserve procedural loops and memory initialization for later lowering.
6. **Emit the linked design.** Walk reachable instances depth first. Detect recursive elaboration by tracking active module specializations and enforce a depth limit for recursion that keeps changing parameter values. Write deterministic JSON containing module specializations, instance paths, parameters, signal types, port bindings, source references, and typed RTL. A missing module, invalid binding, or unsupported construct required for elaboration produces a diagnostic and prevents a successful checkpoint.

## RTL blocks and hard macros

| Input | How it is resolved |
| --- | --- |
| Hierarchical RTL block | Read the block's source through the file list, specialize it per effective parameter set, and elaborate its reachable children. Preserve boundaries such as `riscv_core.u_alu` and `riscv_core.u_rf`. |
| Prebuilt block, SRAM, or other hard macro | Register the module name and its port interface explicitly. The interface can come from a designated Verilog stub or a project port manifest. Record directions, widths, and any supported parameter values. Validate the instance connections and keep it as an opaque leaf with a reference to its model. Liberty and physical views can be associated for later stages. |
| Missing module or macro interface | Fail with the instance path, source location, and unresolved module name. This keeps a missing file from becoming an accidental black box. |

A module name must have one provider. The tool reports a collision if both an RTL definition and a registered macro claim the same name. Supply an interface catalog with `--interfaces Synthesis/setup/cell_interfaces.json` for explicitly instantiated cells or macros. The behavioral RISC-V sources need no catalog. Use `--parameter NAME=EXPR` to override an integral parameter on the top module.

The RISC-V register file, instruction memory, and data memory are RTL blocks. Preserve their unpacked arrays, indexed accesses, and initialization or reset behavior. An external memory macro would need an explicitly registered interface and a design decision about matching its behavior to the RTL.

## First acceptance run

Run the flow on the original RISC-V RTL. Require eight reachable modules including the top and all seven child instances linked. Verify that `riscv_core.u_alu` binds its 5-bit `alu_op` port and retains the 33-bit `tmp` signal, and that the register file and memory arrays retain their declared dimensions. Check that a removed source file, a misspelled child port, and a wrong-width connection in strict mode each produce a source-located error. Repeated runs with identical inputs should produce the same elaborated JSON.

Phase 1 ends with a linked RTL database. Gate mapping, timing constraints, and timing analysis consume that database in later phases.

The implementation covers the project's Verilog subset, including ANSI ports, integral constants and parameters, named and positional bindings, named conditional and loop generate blocks, procedural assignments, event controls, and unpacked arrays. It requires explicit signal declarations and exact port widths in the first version. General SystemVerilog constructs, arbitrary hierarchical expressions, simulation scheduling, and functional equivalence checking require further implementation.

Run the acceptance and diagnostic tests with:

```sh
python3 Synthesis/init_design/test_init_elab.py -v
```
