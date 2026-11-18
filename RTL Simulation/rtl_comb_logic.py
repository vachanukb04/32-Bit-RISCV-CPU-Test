"""Summarize supported combinational RTL as output value decisions."""

from dataclasses import dataclass, field
from itertools import islice

from pyslang import SVInt, ast

from rtl_process_view import expression_label, statements, unconverted
from rtl_structure import VALUE_EXPRESSIONS, signal_refs


@dataclass(frozen=True)
class Value:
    text: str
    inputs: frozenset = frozenset()
    controls: frozenset = frozenset()
    fields: tuple = ()
    retained: bool = False
    source: dict | None = field(default=None, compare=False)
    bindings: tuple = ()


@dataclass(frozen=True)
class Decision:
    kind: str
    selector: Value
    arms: tuple
    fallback: "Value | Decision"
    inputs: frozenset
    controls: frozenset


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
                             previous.retained, source, previous.bindings)
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
                 source=source, bindings=bindings)


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


def rules(node, when=()):
    if isinstance(node, Value):
        bindings = [[name.rsplit(".", 1)[-1], value.text if isinstance(value, Value) else "selected earlier"]
                    for name, value in node.bindings]
        return iter(({"when": list(when), "value": node.text, "fields": node.fields, "locals": bindings,
                      "source": node.source, "retained": node.retained},))

    def choices():
        if node.kind == "if":
            yield from rules(node.arms[0][1], (*when, node.selector.text + " is true"))
            yield from rules(node.fallback, (*when, "otherwise for " + node.selector.text))
        else:
            for label, branch in node.arms:
                yield from rules(branch, (*when, f"{node.kind} {node.selector.text} matches {label}"))
            yield from rules(node.fallback, (*when, f"no {node.kind} match for {node.selector.text}"))

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
                                   controls.union(*(result.controls for result in results)))
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
                              [("", evaluate(part.ifTrue, state))], evaluate(part.ifFalse, state))
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
                    arms.append((labels, evaluate(arm.stmt, state)))
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
        rows = list(islice(rules(outcome), 257))
        if len(rows) > 256:
            return None
        collect_roles(outcome, seen, conditions, dispatches, data_nets)
        dependencies.update(outcome.inputs)
        control_nets.update(outcome.controls)
        symbol = symbols[net]
        outputs.append({"net": net, "name": symbol.name, "bits": symbol.type.bitWidth,
                        "range": str(symbol.type.fixedRange) if symbol.type.bitWidth > 1 else "",
                        "inputs": sorted(outcome.inputs), "controls": sorted(outcome.controls),
                        "rules": rows})
    return {"inputs": sorted(dependencies), "controls": sorted(control_nets), "dataInputs": sorted(data_nets),
            "conditions": list(conditions), "selectors": list(dispatches), "outputs": outputs}
