"""Resolve constants, RTL types, hierarchy and interface bindings."""

import hashlib
import json
import operator
import re

from rtl_analysis import FlowError


MAX_WIDTH = 1_000_000
MAX_GENERATE_ITERATIONS = 4096
ARITHMETIC = set("+ - * / % & | ^ ^~ ~^".split())
COMPARE = set("== != === !== < <= > >=".split())
SHIFT = set("<< >> <<< >>>".split())
LOGIC = {"&&", "||"}
UNARY_CONTEXT = {"+", "-", "~"}


def fail(item, message):
    raise FlowError(message, item["source"])


def shape(width, signed=False, packed=None, dimensions=()):
    return {"width": width, "signed": signed, "packed": packed,
            "dimensions": list(dimensions)}


def vector(width, signed=False):
    return shape(width, signed, [width - 1, 0] if width > 1 else None)


def typed(item, dtype, **fields):
    return {**item, **fields, "type": dtype}


def scalar(item):
    dtype = item["type"]
    if dtype["dimensions"]:
        fail(item, "An unpacked array requires an index in this expression")
    return dtype["width"], dtype["signed"]


def contextual(item, width, signed):
    """Size context-dependent operators while keeping self-determined inputs."""
    old_width, old_signed = scalar(item)
    if width >= old_width:
        kind = item["kind"]
        if kind == "resize" and item["conversion"] != "truncate":
            return contextual(item["value"], width, signed)
        if kind == "binary" and item["op"] in ARITHMETIC | SHIFT:
            fields = {"left": contextual(item["left"], width, signed)}
            if item["op"] in ARITHMETIC:
                fields["right"] = contextual(item["right"], width, signed)
            return typed(item, vector(width, signed), **fields)
        if kind == "unary" and item["op"] in UNARY_CONTEXT:
            return typed(item, vector(width, signed),
                         operand=contextual(item["operand"], width, signed))
        if kind == "ternary":
            return typed(item, vector(width, signed),
                         yes=contextual(item["yes"], width, signed),
                         no=contextual(item["no"], width, signed))
    if (old_width, old_signed) == (width, signed):
        return item
    conversion = ("truncate" if width < old_width else "reinterpret" if width == old_width
                  else "sign_extend" if signed else "zero_extend")
    return {"kind": "resize", "source": item["source"], "type": vector(width, signed),
            "conversion": conversion, "value": item}


def assignment_value(item, width):
    natural, signed = scalar(item)
    return contextual(contextual(item, max(width, natural), signed), width, signed)


def literal(item):
    raw = item["text"].replace("_", "")
    try:
        if "'" not in raw:
            value = int(raw)
            width = max(32, value.bit_length() + 1)
            return typed(item, vector(width, True), value=value)
        width_text, digits = raw.split("'")
        width = int(width_text)
        if not 1 <= width <= MAX_WIDTH:
            fail(item, f"Unsupported literal width {width}")
        signed = digits[0].lower() == "s"
        digits = digits[1:] if signed else digits
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[digits[0].lower()]
        data = digits[1:].lower()
        legal = {2: r"[01xz?]+", 8: r"[0-7xz?]+", 10: r"(?:[0-9]+|[xz?])",
                 16: r"[0-9a-fxz?]+"}[base]
        if not re.fullmatch(legal, data):
            fail(item, f"Invalid based literal {item['text']!r}")
        value = None if re.search(r"[xz?]", data) else int(data, base)
        return typed(item, vector(width, signed), value=value)
    except ValueError:
        fail(item, f"Invalid or oversized literal {item['text']!r}")


def normalize(value, dtype):
    width, signed = dtype["width"], dtype["signed"]
    value &= (1 << width) - 1
    return value - (1 << width) if signed and value >> (width - 1) else value


class NonConstant(Exception):
    pass


def divide(a, b):
    # Verilog integral division truncates toward zero.
    return (-1 if (a < 0) != (b < 0) else 1) * (abs(a) // abs(b))


def const_value(item):
    kind = item["kind"]
    width, signed = scalar(item)
    if kind == "literal" or kind == "name":
        value = item.get("value")
        if value is None:
            raise NonConstant()
    elif kind == "resize":
        source = item["value"]
        value = const_value(source)
        if item["conversion"] == "zero_extend":
            value &= (1 << source["type"]["width"]) - 1
        if item["conversion"] == "sign_extend":
            source_width = source["type"]["width"]
            value = normalize(value, vector(source_width, True))
    elif kind == "unary":
        operand = item["operand"]
        value = const_value(operand)
        op = item["op"]
        if op in UNARY_CONTEXT | {"!"}:
            value = {"+": operator.pos, "-": operator.neg, "~": operator.invert,
                     "!": operator.not_}[op](value)
        else:
            bits = value & ((1 << operand["type"]["width"]) - 1)
            value = (bits == (1 << operand["type"]["width"]) - 1 if op in ("&", "~&")
                     else bool(bits) if op in ("|", "~|") else bits.bit_count() % 2)
            if op in ("~&", "~|", "~^", "^~"):
                value = not value
    elif kind == "binary":
        op = item["op"]
        a, b = const_value(item["left"]), const_value(item["right"])
        if op in SHIFT:
            amount = b & ((1 << item["right"]["type"]["width"]) - 1)
            if amount >= width:
                value = -1 if op == ">>>" and signed and a < 0 else 0
            elif op in ("<<", "<<<"):
                value = a << amount
            elif op == ">>":
                value = (a & ((1 << width) - 1)) >> amount
            else:
                value = a >> amount
        elif op == "%":
            value = a - divide(a, b) * b
        elif op in ("^~", "~^"):
            value = ~(a ^ b)
        else:
            operation = {
                "+": operator.add, "-": operator.sub, "*": operator.mul, "/": divide,
                "&": operator.and_, "|": operator.or_, "^": operator.xor,
                "==": operator.eq, "!=": operator.ne, "===": operator.eq, "!==": operator.ne,
                "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
                "&&": lambda x, y: bool(x) and bool(y), "||": lambda x, y: bool(x) or bool(y),
            }[op]
            value = operation(a, b)
    elif kind == "ternary":
        value = const_value(item["yes"] if const_value(item["condition"]) else item["no"])
    elif kind in ("concat", "replicate"):
        combined = part_width = 0
        for part in item["parts"]:
            bits = part["type"]["width"]
            combined = (combined << bits) | (const_value(part) & ((1 << bits) - 1))
            part_width += bits
        repeats = item.get("repeat", 1)
        value = 0
        for _ in range(repeats):
            value = (value << part_width) | combined
    elif kind in ("index", "slice"):
        base = item["value"]
        value = const_value(base)
        left, right = base["type"]["packed"] or (0, 0)
        index = const_value(item["index"] if kind == "index" else item["lsb"])
        value >>= index - right if left >= right else right - index
    elif kind == "call":
        value = const_value(item["argument"])
        if item["name"] == "$clog2":
            value &= (1 << item["argument"]["type"]["width"]) - 1
            value = (value - 1).bit_length() if value else 0
    else:
        raise NonConstant()
    return normalize(int(value), item["type"])


def require_constant(item, purpose):
    try:
        return const_value(item)
    except (NonConstant, ArithmeticError) as exc:
        raise FlowError(f"{purpose} requires a known integral constant", item["source"]) from exc


def known_index(item, bounds):
    try:
        value = const_value(item)
    except (NonConstant, ArithmeticError):
        return None
    if not min(bounds) <= value <= max(bounds):
        fail(item, f"Index {value} is outside [{bounds[0]}:{bounds[1]}]")
    return value


def expr(item, scope):
    kind = item["kind"]
    if kind == "literal":
        return literal(item)
    if kind == "name":
        symbol = scope.lookup(item["name"], item)
        return typed(item, symbol["type"], symbol=symbol["symbol"],
                     storage=symbol["storage"], direction=symbol.get("direction"),
                     value=symbol.get("value"))
    if kind in ("index", "slice"):
        base = expr(item["value"], scope)
        dtype = base["type"]
        if kind == "index":
            index = expr(item["index"], scope)
            scalar(index)
            if dtype["dimensions"]:
                known_index(index, dtype["dimensions"][0])
                result = {**dtype, "dimensions": dtype["dimensions"][1:]}
            else:
                known_index(index, dtype["packed"] or (0, 0))
                result = vector(1)
            return typed(item, result, value=base, index=index)
        scalar(base)
        msb, lsb = expr(item["msb"], scope), expr(item["lsb"], scope)
        a, b = require_constant(msb, "Part-select bound"), require_constant(lsb, "Part-select bound")
        bounds = dtype["packed"] or (0, 0)
        known_index(msb, bounds)
        known_index(lsb, bounds)
        if a != b and (a < b) != (bounds[0] < bounds[1]):
            fail(item, "Part-select direction disagrees with the declared packed range")
        return typed(item, vector(abs(a-b)+1), value=base, msb=msb, lsb=lsb)
    if kind in ("concat", "replicate"):
        parts = [expr(part, scope) for part in item["parts"]]
        if not parts:
            fail(item, "Empty concatenation or replication")
        repeat = 1
        if kind == "replicate":
            repeat = require_constant(expr(item["count"], scope), "Replication count")
        width = sum(scalar(p)[0] for p in parts) * repeat
        if not 1 <= width <= MAX_WIDTH:
            fail(item, "Replication or concatenation width is out of range")
        return typed(item, vector(width), parts=parts, repeat=repeat)
    if kind == "call":
        arg = expr(item["argument"], scope)
        width, _ = scalar(arg)
        result = typed(item, vector(32, True) if item["name"] == "$clog2"
                       else vector(width, item["name"] == "$signed"), argument=arg)
        if item["name"] == "$clog2":
            require_constant(result, "$clog2")
        return result
    if kind == "unary":
        operand = expr(item["operand"], scope)
        width, signed = scalar(operand)
        return typed(item, vector(width, signed) if item["op"] in UNARY_CONTEXT else vector(1),
                     operand=operand)
    if kind == "binary":
        left, right = expr(item["left"], scope), expr(item["right"], scope)
        lw, ls = scalar(left)
        rw, rs = scalar(right)
        op = item["op"]
        if op in LOGIC:
            return typed(item, vector(1), left=left, right=right)
        if op in SHIFT:
            return typed(item, vector(lw, ls), left=left, right=right)
        common, signed = max(lw, rw), ls and rs
        dtype = vector(1) if op in COMPARE else vector(common, signed)
        return typed(item, dtype, left=contextual(left, common, signed),
                     right=contextual(right, common, signed))
    if kind == "ternary":
        condition = expr(item["condition"], scope)
        scalar(condition)
        yes, no = expr(item["yes"], scope), expr(item["no"], scope)
        yw, ys = scalar(yes)
        nw, ns = scalar(no)
        width, signed = max(yw, nw), ys and ns
        return typed(item, vector(width, signed), condition=condition,
                     yes=contextual(yes, width, signed), no=contextual(no, width, signed))
    fail(item, f"Unsupported expression: {kind}")


class Scope:
    def __init__(self, parent=None, prefix=""):
        self.parent = parent
        self.prefix = prefix
        self.declarations = {}
        self.overrides = {}
        self.resolved = {}
        self.resolving = set()

    def add(self, decl):
        name = decl["name"]
        if name in self.declarations or name in self.resolved:
            fail(decl, f"Duplicate declaration of {name!r}")
        self.declarations[name] = decl

    def lookup(self, name, item):
        if name in self.resolved:
            return self.resolved[name]
        if name not in self.declarations:
            if self.parent is not None:
                return self.parent.lookup(name, item)
            fail(item, f"Undeclared identifier {name!r}")
        decl = self.declarations[name]
        if name in self.resolving:
            fail(decl, f"Cyclic declaration or constant dependency for {name!r}")
        self.resolving.add(name)
        spec = decl["type_spec"]

        def bounds(pair):
            return [require_constant(expr(bound, self), f"Range bound of {name}") for bound in pair]

        packed = bounds(spec["packed"]) if spec["packed"] else None
        dimensions = [bounds(pair) for pair in decl["dimensions"]]
        if packed and spec["storage"] == "integer":
            fail(decl, "Packed range on integer declaration is unsupported")
        if spec["storage"] == "integer":
            packed = [31, 0]
        width = abs(packed[0] - packed[1]) + 1 if packed else 32 if spec["storage"] == "integer" else 1
        if not 1 <= width <= MAX_WIDTH:
            fail(decl, f"Unsupported width {width} for {name!r}")
        dtype = shape(width, spec["signed"], packed, dimensions)
        symbol = {"name": name, "symbol": self.prefix + name, "type": dtype,
                  "storage": spec["storage"], "source": decl["source"], "direction": decl["direction"]}
        if decl["parameter"]:
            if dimensions:
                fail(decl, "Unpacked array parameters are unsupported")
            chosen = self.overrides.get(name)
            value = chosen if chosen is not None else expr(decl["value"], self)
            if packed or spec["storage"]:
                value = assignment_value(value, width)
            else:
                inferred_width, inferred_sign = scalar(value)
                dtype = vector(inferred_width, spec["signed"] if spec["sign"] else inferred_sign)
            symbol.update(type=dtype, storage=decl["parameter"],
                          value=normalize(require_constant(value, f"Parameter {name}"), dtype))
        elif decl["value"] is not None:
            if dimensions:
                fail(decl, "Array declaration initialization is unsupported")
            symbol["initializer"] = assignment_value(expr(decl["value"], self), width)
        self.resolving.remove(name)
        self.resolved[name] = symbol
        return symbol

    def names(self):
        return {**{name: self.lookup(name, item) for name, item in self.declarations.items()},
                **self.resolved}


def targets(item):
    if item["kind"] == "name":
        return [item]
    if item["kind"] in ("index", "slice"):
        return targets(item["value"])
    if item["kind"] == "concat":
        return [target for part in item["parts"] for target in targets(part)]
    fail(item, "Expected a signal, select, or concatenation as a write target")


def validate_target(item, *, procedural):
    for target in targets(item):
        storage = target["storage"]
        if target["direction"] == "input":
            fail(target, f"Cannot drive input port {target['name']!r} in strict mode")
        allowed = ("reg", "integer", "logic") if procedural else ("wire", "logic")
        if storage not in allowed:
            fail(target, f"Invalid write target {target['name']!r} of type {storage}")


def statement(item, scope, procedural=True):
    kind = item["kind"]
    if kind == "assignment":
        lhs = expr(item["lhs"], scope)
        validate_target(lhs, procedural=procedural)
        rhs = assignment_value(expr(item["rhs"], scope), scalar(lhs)[0])
        return {**item, "lhs": lhs, "rhs": rhs}
    if kind == "empty":
        return item
    if kind == "block":
        return {**item, "statements": [statement(child, scope) for child in item["statements"]]}
    if kind == "if":
        condition = expr(item["condition"], scope)
        scalar(condition)
        return {**item, "condition": condition, "yes": statement(item["yes"], scope),
                "no": statement(item["no"], scope) if item["no"] else None}
    if kind == "case":
        selector = expr(item["selector"], scope)
        arms = [{**arm, "labels": [expr(label, scope) for label in arm["labels"]],
                 "body": statement(arm["body"], scope)} for arm in item["arms"]]
        types = [scalar(selector), *(scalar(label) for arm in arms for label in arm["labels"])]
        width = max(t[0] for t in types)
        signed = all(t[1] for t in types)
        return {**item, "selector": contextual(selector, width, signed),
                "arms": [{**arm, "labels": [contextual(label, width, signed) for label in arm["labels"]]}
                         for arm in arms]}
    if kind == "for":
        condition = expr(item["condition"], scope)
        scalar(condition)
        return {**item, "init": statement(item["init"], scope),
                "condition": condition, "step": statement(item["step"], scope),
                "body": statement(item["body"], scope)}
    fail(item, f"Unsupported statement: {kind}")


def bind_order(bindings, formals, item, kind):
    if not bindings:
        return {}
    named = bindings[0]["name"] is not None
    if any((b["name"] is not None) != named for b in bindings):
        fail(item, f"Mixed named and positional {kind} bindings")
    if not named and len(bindings) > len(formals):
        fail(item, f"Too many positional {kind} bindings")
    result = {}
    for i, binding in enumerate(bindings):
        name = binding["name"] if named else formals[i]
        if name not in formals:
            fail(binding, f"Unknown {kind} {name!r} on {item['module']}")
        if name in result:
            fail(binding, f"Duplicate binding for {kind} {name!r}")
        result[name] = binding
    return result


class Elaborator:
    def __init__(self, modules, interfaces, trace):
        self.modules = modules
        self.interfaces = interfaces
        self.trace = trace
        self.definitions = {}
        self.active = []
        self.paths = []
        self.warnings = []

    def specialize(self, module, overrides, site, path):
        if module in self.interfaces:
            if overrides:
                fail(site, f"Macro {module!r} has no parameter interface")
            if module not in self.definitions:
                interface = self.interfaces[module]
                self.trace(f"Registering macro definition {module}: {len(interface['ports'])} signal ports")
                self.definitions[module] = {
                    "id": module, "module": module, "kind": "macro", "source": interface["source"],
                    "parameters": {}, "ports": [
                        {**pin, "type": vector(pin["width"], bool(pin.get("signed"))),
                         "source": interface["source"]} for pin in interface["ports"]
                    ],
                    "power_pins": interface["power_pins"], "signals": [], "children": [], "items": [],
                }
            return self.definitions[module]
        if module not in self.modules:
            fail(site, f"Unresolved module or macro {module!r}")
        definition = self.modules[module]
        items = definition["items"]
        self.trace(f"Resolving declarations and parameters for {path} ({module})")
        scope = Scope()
        declarations = [*definition["parameters"], *definition["ports"],
                        *(item for item in items if item["kind"] == "declaration")]
        for decl in declarations:
            scope.add(decl)
        public = {d["name"] for d in declarations if d["parameter"] == "parameter"}
        for name in overrides:
            if name not in public:
                fail(site, f"Unknown or non-overridable parameter {name!r} on {module}")
        scope.overrides = overrides
        parameters = {d["name"]: scope.lookup(d["name"], d) for d in declarations if d["parameter"]}
        key = module
        if public:
            identity = {n: (p["value"], p["type"]["width"], p["type"]["signed"]) for n, p in parameters.items()}
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
            key = f"{module}__{digest}"
        if key in self.active or len(self.active) >= 128:
            fail(site, f"Recursive or excessively deep hierarchy: {' -> '.join([*self.active, key])}")
        if key in self.definitions:
            self.trace(f"Reusing definition {key} for {path}")
            return self.definitions[key]

        self.active.append(key)
        self.paths.append(path)
        symbols = scope.names()
        block = {
            "id": key, "module": module, "kind": "rtl", "source": definition["source"],
            "parameters": parameters, "ports": [symbols[d["name"]] for d in definition["ports"]],
            "signals": [s for s in symbols.values() if s["direction"] is None and s["storage"]
                        not in ("parameter", "localparam")],
            "children": [], "items": [],
        }
        if parameters:
            values = ", ".join(f"{name}={param['value']}" for name, param in parameters.items())
            self.trace(f"Resolved constants for {path}: {values}")
        for symbol in block["signals"]:
            dtype = symbol["type"]
            if dtype["dimensions"]:
                dimensions = " ".join(f"[{left}:{right}]" for left, right in dtype["dimensions"])
                self.trace(f"Preserved array {path}.{symbol['name']}: {dtype['width']}-bit elements {dimensions}")
        self.region(items, scope, block, block["items"])
        self.paths.pop()
        self.active.pop()
        self.definitions[key] = block
        self.trace(f"Elaborated {path}: {len(block['ports'])} ports, {len(block['signals'])} local signals, "
                   f"{len(block['children'])} direct children")
        return block

    def child(self, item, scope, path):
        module = item["module"]
        site = item["source"]
        self.trace(f"Linking {path} to {module} at {site['file']}:{site['line']}")
        template = self.modules.get(module)
        params = [] if template is None else [
            d["name"] for d in [*template["parameters"], *template["items"]]
            if d.get("parameter") == "parameter"]
        if module not in self.modules and module not in self.interfaces:
            fail(item, f"Unresolved module or macro {module!r} at instance {scope.prefix}{item['name']}")
        overrides = {}
        for name, binding in bind_order(item["parameters"], params, item, "parameter").items():
            value = expr(binding["value"], scope)
            require_constant(value, f"Override of {name}")
            overrides[name] = value
        definition = self.specialize(module, overrides, item, path)
        ports = definition["ports"]
        actuals = bind_order(item["ports"], [p["name"] for p in ports], item, "port")
        bound = []
        for formal in ports:
            name, dtype = formal["name"], formal["type"]
            entry = actuals.get(name)
            at = entry["source"] if entry else item["source"]
            actual = expr(entry["value"], scope) if entry and entry["value"] else None
            if actual is not None:
                atype = actual["type"]
                if (atype["width"], atype["dimensions"]) != (dtype["width"], dtype["dimensions"]):
                    fail(entry, f"Port width or shape mismatch on {item['name']}.{name}: "
                         f"expected {dtype['width']} bits {dtype['dimensions']}, "
                         f"got {atype['width']} bits {atype['dimensions']}")
                if formal["direction"] != "input":
                    validate_target(actual, procedural=False)
                target = actual.get("symbol", actual.get("text", actual["kind"]))
                self.trace(f"Bound {path}.{name} to {target}: {formal['direction']}, {dtype['width']} bits")
            elif formal["direction"] != "output":
                self.warnings.append({"severity": "warning", "stage": "elab", "source": at,
                                      "definition": self.active[-1],
                                      "instance_path": path,
                                      "message": f"Open {formal['direction']} port "
                                                 f"{scope.prefix}{item['name']}.{name}"})
            else:
                self.trace(f"Recorded open output {path}.{name}")
            bound.append({"port": name, "direction": formal["direction"], "type": dtype,
                          "actual": actual, "source": at, "explicit": entry is not None})
        return {"instance": scope.prefix + item["name"], "module": module, "definition": definition["id"],
                "source": item["source"], "bindings": bound}

    def expand_block(self, item, scope, block, result, label, occupied, genvar=None):
        if label in occupied:
            fail(item, f"Duplicate generated scope or instance {label!r}")
        occupied.add(label)
        local = Scope(scope, scope.prefix + label + ".")
        self.trace(f"Expanding generate scope {self.paths[-1]}.{local.prefix.rstrip('.')}")
        if genvar is not None:
            name, value = genvar
            local.resolved[name] = {"name": name, "symbol": local.prefix + name, "type": vector(32, True),
                                    "storage": "localparam", "value": value, "source": item["source"]}
        for child in item["items"]:
            if child["kind"] == "declaration":
                local.add(child)
        symbols = list(local.names().values())
        nested = []
        self.region(item["items"], local, block, nested)
        result.append({"kind": "generate_scope", "name": local.prefix.rstrip("."), "symbols": symbols,
                       "source": item["source"], "items": nested})

    def generate_loop(self, item, scope, block, result, occupied, genvars):
        initial, step = item["init"], item["step"]
        lhs, step_lhs = initial["lhs"], step["lhs"]
        if (lhs["kind"] != "name" or step_lhs["kind"] != "name" or lhs["name"] != step_lhs["name"]
                or initial["op"] != "=" or step["op"] != "="):
            fail(item, "Generate for requires blocking assignments to one genvar")
        name = lhs["name"]
        if not item["inline"] and name not in genvars:
            fail(item, f"Generate loop uses undeclared genvar {name!r}")
        value = require_constant(expr(initial["rhs"], scope), "Generate loop initializer")
        loop = Scope(scope)
        body = item["body"]
        seen = set()
        for _ in range(MAX_GENERATE_ITERATIONS + 1):
            loop.resolved[name] = {"name": name, "symbol": name, "type": vector(32, True),
                                   "storage": "genvar", "value": value}
            if not require_constant(expr(item["condition"], loop), "Generate loop condition"):
                return
            if value in seen or len(seen) == MAX_GENERATE_ITERATIONS:
                fail(item, "Generate loop repeats or exceeds the iteration limit")
            seen.add(value)
            self.expand_block(body, scope, block, result, f"{body['label']}[{value}]",
                              occupied, (name, value))
            value = require_constant(expr(step["rhs"], loop), "Generate loop update")

    def region(self, items, scope, block, result):
        genvars = {name for item in items if item["kind"] == "genvar" for name in item["names"]}
        occupied = set(scope.declarations) | genvars
        for item in items:
            kind = item["kind"]
            if kind in ("declaration", "genvar"):
                continue
            if kind == "instance":
                name = item["name"]
                if name in occupied:
                    fail(item, f"Duplicate instance or declaration {name!r}")
                occupied.add(name)
                path = self.paths[-1] + "." + scope.prefix + name
                try:
                    block["children"].append(self.child(item, scope, path))
                except FlowError as exc:
                    if exc.instance_path is None:
                        exc.instance_path = path
                    raise
            elif kind == "generate_block":
                self.expand_block(item, scope, block, result, item["label"], occupied)
            elif kind == "generate_if":
                chosen = item["yes"] if require_constant(
                    expr(item["condition"], scope), "Generate condition") else item["no"]
                if chosen is not None:
                    self.expand_block(chosen, scope, block, result, chosen["label"], occupied)
            elif kind == "generate_for":
                self.generate_loop(item, scope, block, result, occupied, genvars)
            elif kind == "continuous":
                result.append({**item, "assignment": statement(item["assignment"], scope, False)})
            elif kind == "process":
                events = []
                for event in item["events"]:
                    value = expr(event["expression"], scope) if event["expression"] else None
                    if value is not None:
                        scalar(value)
                    events.append({**event, "expression": value})
                result.append({**item, "events": events, "body": statement(item["body"], scope)})
                site = item["source"]
                controls = ", ".join(
                    event["edge"] if event["expression"] is None else
                    f"{event['edge']} {event['expression'].get('symbol', event['expression']['kind'])}"
                    for event in events)
                detail = f" ({controls})" if controls else ""
                self.trace(f"Preserved {item['process']} at {site['file']}:{site['line']}{detail}")
            else:
                fail(item, f"Unsupported module item: {kind}")

    def elaborate(self, top, overrides):
        for name in self.modules.keys() & self.interfaces.keys():
            fail(self.modules[name], f"Module {name!r} collides with a registered macro interface")
        if top not in self.modules:
            raise FlowError(f"Top module {top!r} is absent from the analyzed RTL")
        try:
            root = self.specialize(top, overrides, self.modules[top], top)
        except FlowError as exc:
            if exc.instance_path is None:
                exc.instance_path = self.paths[-1] if self.paths else top
            raise
        instances = []

        def walk(path, definition, parent=None, instance=None):
            block = self.definitions[definition]
            instances.append({"path": path, "definition": definition, "module": block["module"],
                              "kind": block["kind"], "parent": parent,
                              "bindings": instance["bindings"] if instance else [],
                              "source": instance["source"] if instance else block["source"]})
            for child in block["children"]:
                walk(path + "." + child["instance"], child["definition"], path, child)

        walk(top, root["id"])
        return {"schema_version": 1, "top": top, "top_definition": root["id"],
                "instances": instances, "definitions": self.definitions}
