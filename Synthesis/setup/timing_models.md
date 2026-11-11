# Synthetic LVT, ULVT, and SLVT timing models

[Download the generated Liberty views](synthetic_timing_views.zip) or regenerate them from the repository root:

```sh
python3 Synthesis/setup/generate_timing_library.py --out build/synthesis_timing
```

The package contains 12 Liberty views across the three Vt labels and the four voltage and temperature combinations in [corners.json](corners.json). Each view contains the same 78 base cells. Vt suffixes give variants distinct names, for example `INV_X1_LVT`, `INV_X1_ULVT`, and `INV_X1_SLVT`. Load the three views for one voltage and temperature together to make same-corner Vt swaps possible. The generated `cell_interfaces.json` maps each variant to its base cell and lists its pins. The generated `timing_manifest.json` identifies each view and its model assumptions.

## Timing data

| Arc or check | Data in the model |
| --- | --- |
| Combinational and clock-to-output arcs | NLDM rise/fall delay and output transition indexed by input slew and output load. Combinational unateness follows the Boolean function. Mux data arcs are conditional on the selected input. |
| Flip-flops, latches, and clock gates | Clock-to-output arcs, transparent latch data-to-output arcs, and setup and hold checks against the applicable edge. Scan and enable pins have their own checks. |
| Async reset and set | Assertion-to-output propagation and recovery and removal checks on release. |
| Tie cells | Constant output functions with no propagation arcs. |

Propagation tables use input slew points of 10, 50, and 150 ps and load points of 1, 5, and 15 fF. Constraint tables use related-pin and constrained-pin slew. The Liberty units are ns and pF. The model uses illustrative input pin capacitance and omits pulse-width, noise, variation, power, and physical area data.

| Vt label | Propagation delay and transition multiplier |
| --- | ---: |
| LVT | 1.00 |
| ULVT | 0.88 |
| SLVT | 0.78 |

The generator applies the illustrative temperature factor `1 + 0.001 × (temperature in °C − 25)`. That factor scales propagation and checks. Setup, hold, recovery, and removal use the same assumptions across Vt labels so that tests can isolate a propagation change. The 1 V and 5 V views use identical numerical assumptions at a given temperature. These example numbers and the ordering of the labels are modeling choices for testing. A process node, an exact library revision, and characterized views are required to establish voltage behavior, Vt ordering, and timing for silicon.

## Reference and use

TSMC's public IP Alliance page directs customers to TSMC-Online for foundry-specific offerings. For the structure of the Liberty timing arcs, the generator follows public NLDM examples from the SkyWater PDK. All numerical values in this package are synthetic. [TSMC IP Alliance](https://www.tsmc.com/english/dedicatedFoundry/oip/ip_alliance) · [Inverter timing example](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd/blob/main/cells/inv/sky130_fd_sc_hd__inv_1__tt_025C_1v80.lib.json) · [Flip-flop timing example](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd/blob/main/cells/dfrtp/sky130_fd_sc_hd__dfrtp_1__tt_025C_1v80.lib.json)

Use the package to develop table lookup, arc handling, and Vt swaps in the flow. The manifest marks the views as uncharacterized. For signoff, point the timing flow at approved, process-specific libraries supplied for the design.
