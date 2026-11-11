# Synthetic cell timing and wire RC models

Browse the [individual Liberty files](timing/liberty) or [download the full bundle](synthetic_timing_views.zip). The [scenario manifest](timing/timing_manifest.json) pairs each Liberty file with its RC profile and an example SPEF. Run the generator from the repository root to reproduce the bundle:

```sh
python3 Synthesis/setup/generate_timing_library.py --out build/synthesis_timing
```

## Corner combinations

The filename format is `<process>_<temperature>_<vt>_<voltage>_<rc>.lib`. The current matrix contains **32 Liberty files**.

| Axis | Values |
| --- | --- |
| Process label | `ss` |
| Temperature | `m40c`, `p125c` |
| Vt | `svt`, `lvt`, `ulvt`, `slvt` |
| Supply | `1v`, `5v` |
| RC | `rcbest`, `rcworst` |

For example, `ss_m40c_lvt_1v_rcworst.lib` uses the SS-labeled LVT cell data and the RC-worst wire profile. Each view has 78 cells. The generated [cell interfaces](timing/cell_interfaces.json) map variant names such as `INV_X1_LVT` to their base cells and record their pins. For a single scenario, load the desired Vt variants at one process, temperature, voltage, and RC selection. The two RC-named versions of a given cell corner contain identical Liberty tables and the same internal library name.

## How RC enters timing

| Data | Contents | Where the flow uses it |
| --- | --- | --- |
| Liberty | Cell propagation, output slew, timing checks, input pin capacitance | The timing engine queries cell arcs using input slew and effective load. |
| RC model or extraction deck | Interconnect resistance and capacitance rules | Estimate or extract net parasitics from the routed geometry at a selected RC corner. |
| SPEF | The net's extracted or estimated R and C network | The timing engine uses it for driver load, sink arrival and transition. |

Industry extraction tools apply corner-specific rules to routed geometry and can write the resulting resistance, grounded capacitance, and coupling capacitance in SPEF. STA reads the Liberty and parasitics as separate inputs. Keeping the RC variants out of the cell tables avoids counting wire effects twice. [OpenRCX extraction rules and SPEF](https://openroad.readthedocs.io/en/latest/main/src/rcx/README.html) · [OpenSTA inputs](https://github.com/The-OpenROAD-Project/OpenSTA)

The in-house [RC profiles](rc_models.json) use illustrative resistance and grounded capacitance per micrometer for M1–M3 and resistance per via. The [SPEF generator](generate_rc_spef.py) multiplies by segment length and splits each wire segment's ground capacitance equally across its endpoints. It requires explicit route geometry, models single-driver trees, and leaves cell pin capacitance to Liberty. The model omits coupling and crosstalk. For routed signoff, use the PDK extraction deck and SPEF produced for the actual design.

```sh
python3 Synthesis/setup/generate_rc_spef.py \
  --route Synthesis/setup/example_route.json \
  --corner rcworst --out build/synthesis_timing/rc_chain_rcworst.spef
```

The example route is 50 µm on M1, one VIA12, then 50 µm on M2. Both example SPEFs annotate `n_link` between `u_src:Y` and `u_dst:A` in `rc_chain`.

| Example corner | Series wire and via resistance | Total wire ground capacitance |
| --- | ---: | ---: |
| [RC best](timing/examples/rc_chain_rcbest.spef) | 16.5 Ω | 13.0 fF |
| [RC worst](timing/examples/rc_chain_rcworst.spef) | 25.5 Ω | 19.5 fF |

The example SPEFs demonstrate the interface. A timing run on another design needs matching net and terminal names and its own geometry or extracted SPEF.

## Synthetic cell timing assumptions

Propagation and transition tables use input slew points of 10, 50, and 150 ps and load points of 1, 5, and 15 fF. Constraint tables use related-pin and constrained-pin slew. The models include combinational and clock-to-output arcs, latch data arcs, setup and hold, and async set/reset recovery and removal. They omit pulse-width, noise, variation, power, and physical area data.

| Vt label | Propagation and transition multiplier |
| --- | ---: |
| SVT | 1.12 |
| LVT | 1.00 |
| ULVT | 0.88 |
| SLVT | 0.78 |

The SS label identifies the requested process name. It carries no measured slow-process characterization. The temperature factor is `1 + 0.001 × (temperature in °C − 25)`. At a given temperature, 1 V and 5 V use identical synthetic cell numbers. Setup, hold, recovery, and removal use the same assumptions across Vt labels. These assumptions support flow development. Silicon timing requires the chosen process, characterized libraries, and extracted interconnect.

The timing arc structure follows public Liberty examples. TSMC's public IP Alliance page directs customers to its design portal for foundry-specific offerings. [Inverter timing example](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd/blob/main/cells/inv/sky130_fd_sc_hd__inv_1__tt_025C_1v80.lib.json) · [Flip-flop timing example](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd/blob/main/cells/dfrtp/sky130_fd_sc_hd__dfrtp_1__tt_025C_1v80.lib.json) · [TSMC IP Alliance](https://www.tsmc.com/english/dedicatedFoundry/oip/ip_alliance)
