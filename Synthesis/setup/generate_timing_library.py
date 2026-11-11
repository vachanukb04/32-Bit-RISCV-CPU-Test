"""Generate synthetic NLDM Liberty libraries for exercising the timing flow."""

import argparse
from itertools import product
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from generate_cell_library import SETUP, catalog, json_text, render_liberty


# Illustrative labels and coefficients. These are not process measurements.
VT_DELAY_SCALE = {"LVT": 1.0, "ULVT": 0.88, "SLVT": 0.78}
SLEW_NS = (0.010, 0.050, 0.150)
LOAD_PF = (0.001, 0.005, 0.015)
CHECK_NS = {"setup": 0.028, "hold": 0.010, "recovery": 0.032, "removal": 0.012}


def vector(values):
    return json.dumps(", ".join(f"{value:.6f}" for value in values))


def header():
    return [
        '  delay_model : table_lookup;',
        '  time_unit : "1ns";',
        '  capacitive_load_unit (1, pf);',
        '  input_threshold_pct_rise : 50;',
        '  input_threshold_pct_fall : 50;',
        '  output_threshold_pct_rise : 50;',
        '  output_threshold_pct_fall : 50;',
        '  slew_lower_threshold_pct_rise : 20;',
        '  slew_lower_threshold_pct_fall : 20;',
        '  slew_upper_threshold_pct_rise : 80;',
        '  slew_upper_threshold_pct_fall : 80;',
        '  lu_table_template (propagation) {',
        '    variable_1 : input_net_transition;',
        '    variable_2 : total_output_net_capacitance;',
        f'    index_1 ({vector(SLEW_NS)});',
        f'    index_2 ({vector(LOAD_PF)});',
        '  }',
        '  lu_table_template (constraint) {',
        '    variable_1 : related_pin_transition;',
        '    variable_2 : constrained_pin_transition;',
        f'    index_1 ({vector(SLEW_NS)});',
        f'    index_2 ({vector(SLEW_NS)});',
        '  }',
    ]


def table(name, template, axes, equation):
    rows = ", ".join(vector([equation(x, y) for y in axes[1]]) for x in axes[0])
    return f"        {name} ({template}) {{ values ({rows}); }}"


def propagation(related, kind, sense, base, scale, *, edges=("rise", "fall"), when=None):
    lines = [
        "      timing () {",
        f"        related_pin : {json.dumps(related)};",
        f"        timing_type : {kind};",
        f"        timing_sense : {sense};",
    ]
    if when is not None:
        lines.append(f"        when : {json.dumps(when)};")
    for edge in edges:
        factor = scale * (1.10 if edge == "rise" else 1.0)
        axes = (SLEW_NS, LOAD_PF)
        lines.append(table(f"cell_{edge}", "propagation", axes,
                           lambda slew, load: factor * (base + 0.16 * slew + 1.70 * load)))
        lines.append(table(f"{edge}_transition", "propagation", axes,
                           lambda slew, load: factor * (0.014 + 0.42 * slew + 3.00 * load)))
    return [*lines, "      }"]


def constraint(related, check, clock_edge, scale, *, data_edges=("rise", "fall")):
    lines = [
        "      timing () {",
        f"        related_pin : {json.dumps(related)};",
        f"        timing_type : {check}_{clock_edge};",
    ]
    for edge in data_edges:
        factor = scale * (1.04 if edge == "rise" else 1.0)
        lines.append(table(f"{edge}_constraint", "constraint", (SLEW_NS, SLEW_NS),
                           lambda ref, data: factor * (CHECK_NS[check] + 0.10 * ref + 0.18 * data)))
    return [*lines, "      }"]


def pin_sense(expression, inputs, pin):
    """Enumerate the built-in catalog equation to classify its input polarity."""
    other_pins = [name for name in inputs if name != pin]
    # The catalog supplies fixed Boolean expressions. Only these names and bitwise
    # operators are in scope. Mask the result to treat Python's ~ as one-bit NOT.
    equation = compile(expression.replace("!", "~"), "<cell function>", "eval")
    changes = set()
    for bits in product((0, 1), repeat=len(other_pins)):
        values = dict(zip(other_pins, bits))
        before = eval(equation, {"__builtins__": {}}, dict(values, **{pin: 0})) & 1
        after = eval(equation, {"__builtins__": {}}, dict(values, **{pin: 1})) & 1
        if before != after:
            changes.add(after - before)
    return {frozenset(): None, frozenset({1}): "positive_unate",
            frozenset({-1}): "negative_unate",
            frozenset({-1, 1}): "non_unate"}[frozenset(changes)]


def mux_condition(cell_name, pin):
    if cell_name == "MUX2_X1":
        return {"A": "!S", "B": "S"}.get(pin)
    if cell_name == "MUX4_X1":
        return {"A": "!S0 & !S1", "B": "S0 & !S1",
                "C": "!S0 & S1", "D": "S0 & S1"}.get(pin)
    return None


def timing_for_cell(name, cell, delay_scale, check_scale):
    inputs = cell["inputs"]
    outputs = cell["outputs"]
    pins = {pin: ["      capacitance : 0.001200;"] for pin in inputs}
    pins.update({pin: [] for pin in outputs})
    attributes = []
    if cell["state"] is None:
        base = 0.018 if name in ("INV_X1", "CLKINV_X1") else 0.020 + 0.004 * len(inputs)
        for output, expression in outputs.items():
            for pin in inputs:
                sense = pin_sense(expression, inputs, pin)
                if sense:
                    pins[output].extend(propagation(
                        pin, "combinational", sense, base, delay_scale,
                        when=mux_condition(name, pin)))
        return {"pins": pins, "attributes": attributes}

    model = cell["attributes"]
    if "GCLK" in outputs:
        gate_kind = "latch_posedge_precontrol" if "TE" in inputs else "latch_posedge"
        attributes.append(f"    clock_gating_integrated_cell : {gate_kind};")
        roles = {"CLK": "clock", "E": "enable", "GCLK": "out"}
        if "TE" in inputs:
            roles["TE"] = "test"
        for pin, role in roles.items():
            pins[pin].append(f"      clock_gate_{role}_pin : true;")
        pins["GCLK"].extend(propagation("CLK", "combinational", "positive_unate",
                                       0.035, delay_scale))
        for pin in ("E", "TE"):
            if pin in inputs:
                for check in ("setup", "hold"):
                    pins[pin].extend(constraint("CLK", check, "rising", check_scale))
        return {"pins": pins, "attributes": attributes}

    if cell["state"] == "ff":
        clock = cell["clock"]
        opening = "falling" if model["clocked_on"].startswith("!") else "rising"
        check_edge = opening
        pins["Q"].extend(propagation(clock, f"{opening}_edge", "non_unate", 0.061, delay_scale))
    else:
        clock = "G"
        enable = model["enable"]
        opening = "falling" if enable.startswith("!") else "rising"
        check_edge = "rising" if opening == "falling" else "falling"
        pins[clock].append("      clock : true;")
        pins["Q"].extend(propagation(clock, f"{opening}_edge", "non_unate", 0.044, delay_scale))
        pins["Q"].extend(propagation("D", "combinational", "positive_unate",
                                    0.038, delay_scale, when=enable))

    for pin in ("D", "SI", "SE", "E"):
        if pin in inputs:
            for check in ("setup", "hold"):
                pins[pin].extend(constraint(clock, check, check_edge, check_scale))

    for kind, output_edge in (("clear", "fall"), ("preset", "rise")):
        if kind not in model:
            continue
        active_low = model[kind].startswith("!")
        pin = model[kind].lstrip("!")
        assertion_edge = "fall" if active_low else "rise"
        release_edge = "rise" if active_low else "fall"
        sense = "positive_unate" if assertion_edge == output_edge else "negative_unate"
        when = "RN" if kind == "preset" and "clear" in model else None
        pins["Q"].extend(propagation(pin, kind, sense, 0.046, delay_scale,
                                    edges=(output_edge,), when=when))
        for check in ("recovery", "removal"):
            pins[pin].extend(constraint(clock, check, check_edge, check_scale,
                                        data_edges=(release_edge,)))
    return {"pins": pins, "attributes": attributes}


def generate():
    base_cells = catalog()
    base_corners = json.loads((SETUP / "corners.json").read_text(encoding="utf-8"))["corners"]
    files = {}
    interface = {"schema_version": 1, "library_kind": "synthetic_nldm", "cells": {}}
    manifest = {
        "schema_version": 1,
        "library_kind": "synthetic_nldm",
        "timing_characterized": False,
        "time_unit": "ns",
        "capacitance_unit": "pF",
        "cell_interfaces": "cell_interfaces.json",
        "views": {},
    }
    for vt, multiplier in VT_DELAY_SCALE.items():
        suffix = "_" + vt
        cells = {name + suffix: cell for name, cell in base_cells.items()}
        for name, cell in base_cells.items():
            interface["cells"][name + suffix] = {
                "base_cell": name,
                "vt": vt,
                "ports": [
                    {"name": pin, "direction": direction, "width": 1}
                    for direction, pins in (("input", cell["inputs"]), ("output", cell["outputs"]))
                    for pin in pins
                ],
                "power_pins": ["VDD", "VSS"],
            }
        for corner_id, corner in base_corners.items():
            voltage = corner["supply_voltage_v"]
            temperature = corner["temperature_c"]
            temperature_scale = 1 + 0.001 * (temperature - 25)
            delay_scale = temperature_scale * multiplier
            # The voltage is metadata in this proxy. Constraint values vary
            # with temperature only so tests can isolate propagation changes.
            model = {"header": header(), "cells": {
                name + suffix: timing_for_cell(name, cell, delay_scale, temperature_scale)
                for name, cell in base_cells.items()
            }}
            view_id = f"{corner_id}_{vt.lower()}"
            path = f"liberty/stdcells_{view_id}.lib"
            files[path] = render_liberty(cells, view_id, voltage, temperature, timing=model)
            manifest["views"][view_id] = {
                "vt": vt,
                "supply_voltage_v": voltage,
                "temperature_c": temperature,
                "liberty": path,
                "propagation_scale": delay_scale,
                "constraint_scale": temperature_scale,
            }
    files["timing_manifest.json"] = json_text(manifest)
    files["cell_interfaces.json"] = json_text(interface)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("build/synthesis_timing"),
                        help="Directory for the generated Liberty views and manifests.")
    parser.add_argument("--archive", type=Path, help="Also write a ZIP of the generated files.")
    options = parser.parse_args()
    files = generate()
    for name, content in files.items():
        target = options.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    if options.archive:
        options.archive.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(options.archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in sorted(files.items()):
                info = ZipInfo(name)
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)
    print(f"Wrote {sum(name.endswith('.lib') for name in files)} synthetic Liberty views to {options.out}")


if __name__ == "__main__":
    main()
