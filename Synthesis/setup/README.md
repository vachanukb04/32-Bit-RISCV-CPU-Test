# Synthesis setup

[riscv_core.filelist](riscv_core.filelist) lists the eight RTL sources for the CPU. The planned `init` command expands `${REPO_ROOT}` to the absolute path of the checkout.

## Cell library and corners

This setup contains 78 original functional cell models in Liberty format. They cover buffers and inverters, basic gates, compound AO/OA gates, multiplexers, adders, tie cells, flip-flops, scan flip-flops, latches, and integrated clock gates. Each has one nominal variant. A physical library's cell catalog, drive strengths, timing, area, and power depend on its process and characterization.

| Setup ID | VDD | Temperature | Library use |
| --- | ---: | ---: | --- |
| `1v00_n40c` | 1.00 V | −40 °C | [Functional Liberty view](liberty/stdcells_1v00_n40c.lib) |
| `1v00_p125c` | 1.00 V | +125 °C | [Functional Liberty view](liberty/stdcells_1v00_p125c.lib) |
| `5v00_n40c` | 5.00 V | −40 °C | [Functional Liberty view](liberty/stdcells_5v00_n40c.lib) |
| `5v00_p125c` | 5.00 V | +125 °C | [Functional Liberty view](liberty/stdcells_5v00_p125c.lib) |

VSS is 0 V in all four views. The views share the same logic and pin definitions and support functional mapping. Their voltage and temperature values identify the requested setups. Timing, area, and power characterization requires process-specific models.

The [synthetic timing models](timing_models.md) add LVT, ULVT, and SLVT NLDM views for flow testing.

For a published 5 V family, GF180MCU 7-track provides characterized 5.00 V at 25 °C and 4.50 V or 5.50 V views at −40 °C and +125 °C. Exact 5.00 V timing at both requested temperatures requires matching characterized data. [Published GF180MCU Liberty views](https://github.com/google/globalfoundries-pdk-libs-gf180mcu_fd_sc_mcu7t5v0/tree/main/liberty)

## Flow inputs

- **Init** will read [corners.json](corners.json), which defines corner IDs, library paths relative to this directory, and whether each setup allows functional mapping.
- **Elab** will use [cell_interfaces.json](cell_interfaces.json) to resolve explicitly instantiated cells as leaves and check signal pin names, directions, and widths. Power and ground pins are recorded separately. The RISC-V design's behavioral RTL and inferred memories remain RTL at this stage.
- **Compile** will use the selected powered Liberty view for Boolean and sequential matching. STA and timing-driven mapping require characterized Liberty data from a chosen process.

For cells with both asynchronous set and reset, the functional model gives reset priority. The active-low `RN` and `SN` pins encode assertion polarity.

Update [generate_cell_library.py](generate_cell_library.py) to change the cell catalog and regenerate the views and manifests together:

```sh
python3 Synthesis/setup/generate_cell_library.py
python3 Synthesis/setup/generate_cell_library.py --check
```
