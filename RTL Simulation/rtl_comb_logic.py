"""Summarize supported combinational RTL as output value decisions."""

from dataclasses import dataclass, field
from itertools import islice

from pyslang import SVInt, ast

from rtl_process_view import expression_label, statements, unconverted
from rtl_schematic import Drawing
from rtl_structure import VALUE_EXPRESSIONS, integer_literal, signal_refs


@dataclass(frozen=True)
class Value:
    text: str
    inputs: frozenset = frozenset()
    controls: frozenset = frozenset()
    fields: tuple = ()
    retained: bool = False
    source: dict | None = field(default=None, compare=False)
    bindings: tuple = ()
    expression: ast.Expression | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Match:
    label: str
    values: tuple
    keys: tuple


@dataclass(frozen=True)
class Decision:
    kind: str
    selector: Value
    arms: tuple
    fallback: "Value | Decision"
    inputs: frozenset
    controls: frozenset
    priority: bool = False


class UnsupportedProcess(Exception):
    pass


def input_dependencies(expr, state):
    data, controls = set(), set()
    for net in signal_refs(expr):
        previous = state.get(net)
        if previous is None:
            data.add(net)
        else:
            data.update(previous.inputs)
            controls.update(previous.controls)
    return frozenset(data), frozenset(controls)


def bit_fields(expr, text):
    bare = unconverted(expr)
    if not isinstance(bare, (ast.ConcatenationExpression, ast.ReplicationExpression)) or not bare.type.isIntegral:
        return ()
    width = bare.type.bitWidth
    operands = bare.operands if isinstance(bare, ast.ConcatenationExpression) else [bare]
    result = []
    for operand in operands:
        if operand.type.bitWidth:
            low = width - operand.type.bitWidth
            result.append((f"[{width - 1}:{low}]" if width - low > 1 else f"[{low}]",
                           expression_label(operand, text)))
            width = low
    return tuple(result)


def value(expr, state, locate, text, reuse_decision=False):
    bare = unconverted(expr)
    source = locate(expr.sourceRange.start) if expr.sourceRange.start else None
    if isinstance(bare, VALUE_EXPRESSIONS):
        previous = state.get(bare.symbol.hierarchicalPath)
        if previous is not None and bare.type.bitWidth == expr.type.bitWidth and bare.type.isSigned == expr.type.isSigned:
            if isinstance(previous, Value):
                return Value(previous.text, previous.inputs, previous.controls, previous.fields,
                             previous.retained, source, previous.bindings, previous.expression)
            if reuse_decision:
                return previous

    inputs, controls = input_dependencies(expr, state)
    selectors = set(controls)

    def visit(node):
        if isinstance(node, ast.CallExpression) and getattr(node, "subroutineName", "") not in {"$signed", "$unsigned"}:
            raise UnsupportedProcess()
        if isinstance(node, ast.AssignmentExpression):
            raise UnsupportedProcess()
        if isinstance(node, ast.ConditionalExpression):
            for condition in node.conditions:
                if condition.pattern is not None:
                    raise UnsupportedProcess()
                left, right = input_dependencies(condition.expr, state)
                selectors.update(left | right)

    bare.visit(visit)
    bindings = tuple((net, state[net]) for net in signal_refs(expr) if net in state)
    return Value(expression_label(expr, text), inputs, frozenset(selectors), bit_fields(expr, text),
                 source=source, bindings=bindings, expression=expr)


def case_label(expr, fallback):
    bare = unconverted(expr)
    symbol = bare.symbol if isinstance(bare, VALUE_EXPRESSIONS) else None
    if not isinstance(symbol, ast.ParameterSymbol):
        return fallback
    val = symbol.value.value
    bits = symbol.type.bitWidth
    if isinstance(val, SVInt) and not val.hasUnknown and 0 < bits <= 64:
        encoded = int(val) & ((1 << bits) - 1)
        return f"{fallback} ({bits}'b{encoded:0{bits}b})"
    return fallback


def case_key(expr, selector_width):
    if expr.type.bitWidth != selector_width:
        return None
    value = integer_literal(expr)
    bare = unconverted(expr)
    if (value is None and bare.type.bitWidth == selector_width and bare.type.isSigned == expr.type.isSigned and
            isinstance(bare, VALUE_EXPRESSIONS) and isinstance(bare.symbol, ast.ParameterSymbol)):
        item = bare.symbol.value.value
        if isinstance(item, SVInt) and not item.hasUnknown:
            value = int(item)
    bits = expr.type.bitWidth
    return (bits, value & ((1 << bits) - 1)) if value is not None and bits else None


class RuleDrawing(Drawing):
    def __init__(self, text):
        super().__init__(text, self.resolve)
        self.locals = {}
        self.cached = {}

    def resolve(self, expr):
        if isinstance(expr, VALUE_EXPRESSIONS):
            outcome = self.locals.get(expr.symbol.hierarchicalPath)
            if outcome is not None:
                return self.outcome(outcome)
        if isinstance(expr, (ast.RangeSelectExpression, ast.ElementSelectExpression)):
            base = unconverted(expr.value)
            if isinstance(base, VALUE_EXPRESSIONS) and base.symbol.hierarchicalPath in self.locals:
                inputs = [("A", self.outcome(self.locals[base.symbol.hierarchicalPath]))]
                # Constant selections are fully described by their range label.
                indices = ([expr.left, expr.right] if isinstance(expr, ast.RangeSelectExpression)
                           else [expr.selector])
                for index in indices:
                    if signal_refs(index):
                        inputs.append((str(len(inputs)), self.expression(index)))
                label = expression_label(expr, self.text)
                if len(inputs) == 1:
                    return self.add("operator", label, inputs, symbol="[:]", bits=expr.type.bitWidth)
                return self.add("expression", label, [(str(i + 1), node) for i, (_, node) in enumerate(inputs)])
        if isinstance(expr, ast.CallExpression) and expr.subroutineName in {"$signed", "$unsigned"}:
            return self.add("operator", expr.subroutineName, [("A", self.expression(expr.arguments[0]))],
                            symbol="cast", bits=expr.type.bitWidth)
        return None

    def outcome(self, value):
        key = id(value)
        if key in self.cached:
            return self.cached[key]
        if isinstance(value, Value):
            if value.expression is None:
                result = self.terminal(value.text, sorted(value.inputs))
            else:
                previous = self.locals
                self.locals = dict(value.bindings)
                try:
                    result = self.expression(value.expression)
                finally:
                    self.locals = previous
        else:
            result = self.outcome(value.fallback)
            for index in reversed(range(len(value.arms))):
                condition = self.arm_match(value, index)
                result = self.mux(result, self.outcome(value.arms[index][1]), condition)
        self.cached[key] = result
        return result

    def combine(self, gates, label):
        if not gates:
            return self.terminal("1'b0" if label == "OR" else "1'b1")
        if len(gates) == 1:
            return gates[0]
        middle = len(gates) // 2
        return self.add("gate", label, [("A", self.combine(gates[:middle], label)),
                                        ("B", self.combine(gates[middle:], label))], bits=1)

    def arm_match(self, decision, index):
        selector = self.outcome(decision.selector)
        if decision.kind == "if":
            return self.add("operator", "If condition is true", [("A", selector)], symbol="true", bits=1)
        compare = {"case": ("Case equality", "==="), "casez": ("Casez match", "?="),
                   "casex": ("Casex match", "?=")}[decision.kind]
        match = decision.arms[index][0]
        tests = [self.add("operator", compare[0], [("A", selector), ("B", self.terminal(case_label(value.expression, value.text)))],
                          symbol=compare[1], bits=1) for value in match.values]
        return self.combine(tests, "OR")

    def selected(self, decision, index):
        count = len(decision.arms)
        if decision.kind == "if" and index == 0:
            return self.arm_match(decision, 0)
        terms = []
        if index < count:
            terms.append(self.arm_match(decision, index))
        if index == count or (decision.priority and index > 0):
            earlier = self.combine([self.arm_match(decision, prev) for prev in range(index)], "OR")
            terms.append(self.add("gate", "NOT", [("A", earlier)], bits=1))
        return self.combine(terms, "AND")


def data_circuit(value, symbol, text, source):
    drawing = RuleDrawing(text)
    driver = drawing.outcome(value)
    # Keep a distinct endpoint for an output whose previous value is retained.
    sink = drawing.output(symbol.name, driver, ["$result"])
    return drawing.finish(symbol.name, source, sink)


def rule_circuits(leaf, path, symbol, text):
    circuits = {"data": data_circuit(leaf, symbol, text, leaf.source)}
    if path:
        control = RuleDrawing(text)
        enable = control.combine([control.selected(decision, index) for decision, index in path], "AND")
        sink = control.output("Select this rule", enable, ["$selected"])
        circuits["selection"] = control.finish("Select this rule", leaf.source, sink)
    return circuits


def rules(node, symbol, text, when=(), path=()):
    if isinstance(node, Value):
        bindings = [[name.rsplit(".", 1)[-1], value.text if isinstance(value, Value) else "selected earlier"]
                    for name, value in node.bindings]
        return iter(({"when": list(when), "value": node.text, "fields": node.fields, "locals": bindings,
                      "source": node.source, "retained": node.retained,
                      "circuits": rule_circuits(node, path, symbol, text)},))

    def choices():
        if node.kind == "if":
            yield from rules(node.arms[0][1], symbol, text, (*when, node.selector.text + " is true"),
                             (*path, (node, 0)))
            yield from rules(node.fallback, symbol, text, (*when, "otherwise for " + node.selector.text),
                             (*path, (node, 1)))
        else:
            for index, (match, branch) in enumerate(node.arms):
                yield from rules(branch, symbol, text,
                                 (*when, f"{node.kind} {node.selector.text} matches {match.label}"),
                                 (*path, (node, index)))
            yield from rules(node.fallback, symbol, text, (*when, f"no {node.kind} match for {node.selector.text}"),
                             (*path, (node, len(node.arms))))

    return choices()


def collect_roles(node, seen, conditions, dispatches, data_nets):
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, Value):
        data_nets.update(node.inputs - node.controls)
        return
    destination = conditions if node.kind == "if" else dispatches
    destination.setdefault(node.selector.text, None)
    for _, branch in node.arms:
        collect_roles(branch, seen, conditions, dispatches, data_nets)
    collect_roles(node.fallback, seen, conditions, dispatches, data_nets)


def summarize_combinational(body, locate, text):
    symbols = {}
    initial = {}

    def stored(net, state):
        if net not in state:
            initial.setdefault(net, Value("Retain " + symbols[net].name, frozenset({net}), retained=True))
        return state[net] if net in state else initial[net]

    def merge(state, kind, control, arms, otherwise):
        keys = [key for match, _ in arms if match for key in match.keys]
        priority = kind != "case" or None in keys or len(keys) != len(set(keys))
        merged = state.copy()
        written = dict.fromkeys(net for _, branch in arms for net in branch)
        written.update(dict.fromkeys(otherwise))
        for net in written:
            fallback = stored(net, otherwise)
            choices = tuple((label, stored(net, branch)) for label, branch in arms)
            if all(outcome == fallback for _, outcome in choices):
                merged[net] = fallback
                continue
            results = [*(outcome for _, outcome in choices), fallback]
            controls = control.inputs | control.controls
            merged[net] = Decision(kind, control, choices, fallback,
                                   controls.union(*(result.inputs for result in results)),
                                   controls.union(*(result.controls for result in results)), priority)
        return merged

    def evaluate(stmt, state):
        state = state.copy()
        for part in statements(stmt):
            if isinstance(part, ast.TimedStatement):
                # The outer sensitivity event has already been identified as combinational.
                raise UnsupportedProcess()
            if isinstance(part, ast.ConditionalStatement):
                if len(part.conditions) != 1 or part.conditions[0].pattern is not None:
                    raise UnsupportedProcess()
                predicate = value(part.conditions[0].expr, state, locate, text)
                state = merge(state, "if", predicate,
                              [(None, evaluate(part.ifTrue, state))], evaluate(part.ifFalse, state))
            elif isinstance(part, ast.CaseStatement):
                kinds = {"Normal": "case", "WildcardXOrZ": "casex", "WildcardJustZ": "casez"}
                kind = kinds.get(part.condition.name)
                if kind is None:
                    raise UnsupportedProcess()
                select = value(part.expr, state, locate, text)
                arms = []
                for arm in part.items:
                    matches = [value(expr, state, locate, text) for expr in arm.expressions]
                    if any(match.inputs or match.controls for match in matches):
                        raise UnsupportedProcess()
                    labels = ", ".join(case_label(expr, match.text)
                                       for expr, match in zip(arm.expressions, matches))
                    keys = tuple(case_key(expr, part.expr.type.bitWidth) for expr in arm.expressions)
                    match = Match(labels, tuple(matches), keys)
                    arms.append((match, evaluate(arm.stmt, state)))
                state = merge(state, kind, select, arms, evaluate(part.defaultCase, state))
            elif isinstance(part, ast.ExpressionStatement) and isinstance(part.expr, ast.AssignmentExpression):
                item = part.expr
                if (not isinstance(item.left, VALUE_EXPRESSIONS) or not item.left.type.isIntegral or
                        item.isCompound or item.isNonBlocking or item.timingControl is not None):
                    raise UnsupportedProcess()
                symbol = item.left.symbol
                net = symbol.hierarchicalPath
                symbols[net] = symbol
                state[net] = value(item.right, state, locate, text, reuse_decision=True)
            else:
                raise UnsupportedProcess()
        return state

    try:
        result = evaluate(body.stmt if isinstance(body, ast.TimedStatement) else body, {})
    except UnsupportedProcess:
        return None
    if not result:
        return None

    conditions, dispatches, seen = {}, {}, set()
    outputs, dependencies, control_nets, data_nets = [], set(), set(), set()
    for net, outcome in result.items():
        symbol = symbols[net]
        rows = list(islice(rules(outcome, symbol, text), 257))
        if len(rows) > 256:
            return None
        collect_roles(outcome, seen, conditions, dispatches, data_nets)
        dependencies.update(outcome.inputs)
        control_nets.update(outcome.controls)
        outputs.append({"net": net, "name": symbol.name, "bits": symbol.type.bitWidth,
                        "range": str(symbol.type.fixedRange) if symbol.type.bitWidth > 1 else "",
                        "inputs": sorted(outcome.inputs), "controls": sorted(outcome.controls),
                        "circuit": data_circuit(outcome, symbol, text, rows[0]["source"]),
                        "rules": rows})
    return {"inputs": sorted(dependencies), "controls": sorted(control_nets), "dataInputs": sorted(data_nets),
            "conditions": list(conditions), "selectors": list(dispatches), "outputs": outputs}
