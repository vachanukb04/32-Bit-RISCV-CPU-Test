"""Write synthetic grounded wire parasitics from explicit routed geometry."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re


SETUP = Path(__file__).resolve().parent
NAME = r"[A-Za-z_][A-Za-z0-9_./\[\]]*"
TERMINAL = rf"{NAME}:{NAME}"
RC_MODEL_FILE = SETUP / "rc_models.json"
EXAMPLE_ROUTE_FILE = SETUP / "example_route.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_name(value, pattern=NAME):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Unsupported SPEF name: {value!r}")
    return value


def terminal(entry, driver):
    kind = entry["kind"]
    if kind == "port":
        return checked_name(entry["node"]), "*P", "I" if driver else "O"
    if kind == "instance":
        return checked_name(entry["node"], TERMINAL), "*I", "O" if driver else "I"
    raise ValueError(f"Unsupported terminal kind: {kind!r}")


def positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return value


def net_section(net, model):
    name = checked_name(net["name"])
    pins = [terminal(net["driver"], True), *(terminal(s, False) for s in net["sinks"])]
    if len(pins) < 2 or len({pin for pin, _, _ in pins}) != len(pins):
        raise ValueError(f"{name}: expected a driver and distinct sinks")
    pin_names = {pin for pin, _, _ in pins}
    capacitance = defaultdict(float)
    neighbors = defaultdict(set)
    resistors = []
    for segment in net["segments"]:
        a, b = segment["from"], segment["to"]
        for endpoint in (a, b):
            if endpoint not in pin_names:
                checked_name(endpoint, rf"{re.escape(name)}:[1-9][0-9]*")
        if a == b or b in neighbors[a]:
            raise ValueError(f"{name}: duplicate or self-connected segment {a} to {b}")
        neighbors[a].add(b)
        neighbors[b].add(a)

        if "via" in segment:
            if "layer" in segment or "length_um" in segment:
                raise ValueError(f"{name}: a via segment cannot also specify a wire")
            via = segment["via"]
            resistance = positive(model["via_resistance_ohm"][via], f"{via} resistance")
        else:
            layer = segment["layer"]
            length = positive(segment["length_um"], f"{layer} length")
            coeff = model["layers"][layer]
            resistance = positive(coeff["r_ohm_per_um"], f"{layer} R/length") * length
            cap = positive(coeff["c_ground_ff_per_um"], f"{layer} C/length") * length
            capacitance[a] += cap / 2
            capacitance[b] += cap / 2
        resistors.append((a, b, resistance))

    seen = set()
    pending = [pins[0][0]]
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            if node in neighbors:
                pending.extend(neighbors[node] - seen)
    if seen != set(neighbors) or not pin_names <= seen or len(resistors) != len(seen) - 1:
        raise ValueError(f"{name}: route must be a connected tree containing every terminal")

    lines = [f"*D_NET {name} {sum(capacitance.values()):.9g}", "*CONN"]
    lines.extend(f"{kind} {pin} {direction}" for pin, kind, direction in pins)
    lines.append("*CAP")
    lines.extend(f"{i} {node} {cap:.9g}" for i, (node, cap) in
                 enumerate(sorted(capacitance.items()), 1))
    lines.append("*RES")
    lines.extend(f"{i} {a} {b} {resistance:.9g}" for i, (a, b, resistance) in
                 enumerate(resistors, 1))
    return [*lines, "*END", ""], pins


def render_spef(route, model, corner):
    design = checked_name(route["design"])
    lines = [
        '*SPEF "IEEE 1481-1998"',
        f'*DESIGN "{design}"',
        '*DATE "Synthetic example"',
        '*VENDOR "In-house synthesis flow"',
        '*PROGRAM "generate_rc_spef.py"',
        '*VERSION "1.0"',
        '*DESIGN_FLOW "PIN_CAP NONE"',
        '*DIVIDER /',
        '*DELIMITER :',
        '*BUS_DELIMITER [ ]',
        '*T_UNIT 1 NS',
        '*C_UNIT 1 FF',
        '*R_UNIT 1 OHM',
        '*L_UNIT 1 HENRY',
        "",
        f"// {corner}: synthetic grounded wire RC",
        "",
    ]
    ports, sections, net_names, pins_seen = {}, [], set(), set()
    for net in route["nets"]:
        name = net["name"]
        if name in net_names:
            raise ValueError(f"Duplicate net: {name}")
        net_names.add(name)
        section, pins = net_section(net, model)
        for pin, kind, direction in pins:
            if pin in pins_seen:
                raise ValueError(f"Terminal belongs to multiple nets: {pin}")
            pins_seen.add(pin)
            if kind == "*P":
                ports[pin] = direction
        sections.extend(section)
    if not sections:
        raise ValueError("No routed nets found")
    if ports:
        lines.append("*PORTS")
        lines.extend(f"{port} {direction}" for port, direction in sorted(ports.items()))
        lines.append("")
    return "\n".join([*lines, *sections])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--corner", choices=("rcbest", "rcworst"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    try:
        model = read_json(RC_MODEL_FILE)["corners"][options.corner]
        content = render_spef(read_json(options.route), model, options.corner)
    except (KeyError, OSError, ValueError) as error:
        parser.error(str(error))
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
