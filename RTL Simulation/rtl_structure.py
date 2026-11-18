"""Describe hardware constructs visible in an elaborated RTL scope."""

from collections import defaultdict
from functools import reduce

import pyslang
from pyslang import ast


VALUE_EXPRESSIONS = (ast.NamedValueExpression, ast.HierarchicalValueExpression)
SIGNAL_TYPES = {"Net", "Variable", "FormalArgument"}
BITWISE = {
    "BinaryAnd": "AND", "BinaryOr": "OR", "BinaryXor": "XOR", "BinaryXnor": "XNOR",
    "BitwiseNot": "NOT", "BitwiseAnd": "Reduction AND", "BitwiseOr": "Reduction OR",
    "BitwiseXor": "Reduction XOR", "BitwiseNand": "Reduction NAND",
    "BitwiseNor": "Reduction NOR", "BitwiseXnor": "Reduction XNOR",
    "LogicalAnd": "Logical AND", "LogicalOr": "Logical OR", "LogicalNot": "Logical NOT",
}
OPERATORS = {
    "Add": "Adder", "Subtract": "Subtractor", "Multiply": "Multiplier",
    "Divide": "Divider", "Mod": "Remainder", "Power": "Exponentiation", "Minus": "Negation",
    "Equality": "Equal", "Inequality": "Not equal", "CaseEquality": "Case equality",
    "CaseInequality": "Case inequality", "LessThan": "Less than", "GreaterThan": "Greater than",
    "LessThanEqual": "Less than or equal", "GreaterThanEqual": "Greater than or equal",
    "LogicalShiftLeft": "Left shift", "LogicalShiftRight": "Right shift",
    "ArithmeticShiftLeft": "Arithmetic left shift", "ArithmeticShiftRight": "Arithmetic right shift",
}


def signal_refs(item):
    names = set()
    if item is not None:
        def collect(node):
            if isinstance(node, VALUE_EXPRESSIONS) and node.symbol.kind.name in SIGNAL_TYPES:
                names.add(node.symbol.hierarchicalPath)
        item.visit(collect)
    return sorted(names)


def targets(expr):
    """Return lvalue symbols and the selected expression, excluding index operands."""
    if isinstance(expr, ast.ConcatenationExpression):
        for operand in expr.operands:
            yield from targets(operand)
        return
    symbol = expr.getSymbolReference()
    if symbol is not None and symbol.kind.name in SIGNAL_TYPES:
        yield symbol, expr


def assignments(item):
    found = []

    def collect(node):
        if isinstance(node, ast.ForLoopStatement):
            node.body.visit(collect)
            return ast.VisitAction.Skip
        if isinstance(node, ast.AssignmentExpression):
            found.append(node)

    item.visit(collect)
    return found


def access(item, writes):
    if item is None:
        return []
    names = set(signal_refs(item))
    # Preserve reads of values that are also assigned, including RHS self-references.
    reads = set()
    for assignment in writes:
        reads.update(signal_refs(assignment.right))
        lhs = {symbol.hierarchicalPath for symbol, _ in targets(assignment.left)}
        left_refs = set(signal_refs(assignment.left))
        reads.update(left_refs if assignment.isCompound else left_refs - lhs)
    def collect(node):
        if isinstance(node, ast.AssignmentExpression):
            return ast.VisitAction.Skip
        if isinstance(node, VALUE_EXPRESSIONS) and node.symbol.kind.name in SIGNAL_TYPES:
            reads.add(node.symbol.hierarchicalPath)
    item.visit(collect)
    return sorted(names & reads)


def span(dtype):
    if not dtype.isIntegral or dtype.bitWidth < 2:
        return ""
    bounds = dtype.fixedRange
    if abs(bounds.left - bounds.right) + 1 != dtype.bitWidth:
        return f"[0..{dtype.bitWidth - 1}]"
    return f"[{min(bounds.left, bounds.right)}..{max(bounds.left, bounds.right)}]"


def integer_literal(expr):
    value = expr.constant
    if value is not None and isinstance(value.value, pyslang.SVInt) and not value.value.hasUnknown:
        return int(value.value)
    if isinstance(expr, ast.IntegerLiteral) and not expr.value.hasUnknown:
        return int(expr.value)
    return None


def coverage(symbol, expr):
    """A mask of definitely selected bits relative to the symbol's low index."""
    dtype = symbol.type
    if not dtype.isIntegral:
        return 0
    if isinstance(expr, VALUE_EXPRESSIONS):
        return (1 << dtype.bitWidth) - 1
    low = min(dtype.fixedRange.left, dtype.fixedRange.right)
    if isinstance(expr, ast.ElementSelectExpression) and isinstance(expr.value, VALUE_EXPRESSIONS):
        idx = integer_literal(expr.selector)
        if idx is not None and 0 <= idx - low < dtype.bitWidth:
            return 1 << (idx - low)
    if (isinstance(expr, ast.RangeSelectExpression) and isinstance(expr.value, VALUE_EXPRESSIONS)
            and expr.selectionKind.name == "Simple"):
        left, right = integer_literal(expr.left), integer_literal(expr.right)
        if left is not None and right is not None:
            start, end = min(left, right) - low, max(left, right) - low
            if 0 <= start <= end < dtype.bitWidth:
                return ((1 << (end - start + 1)) - 1) << start
    return 0


def definite_writes(stmt):
    """Compute per-signal bit coverage guaranteed on every supported control path."""
    if isinstance(stmt, ast.BlockStatement):
        return definite_writes(stmt.body)
    if isinstance(stmt, ast.TimedStatement):
        return definite_writes(stmt.stmt)
    if isinstance(stmt, ast.StatementList):
        result = defaultdict(int)
        for child in stmt.list:
            for name, mask in definite_writes(child).items():
                result[name] |= mask
        return result
    if isinstance(stmt, ast.ExpressionStatement) and isinstance(stmt.expr, ast.AssignmentExpression):
        result = defaultdict(int)
        for symbol, expr in targets(stmt.expr.left):
            result[symbol.hierarchicalPath] |= coverage(symbol, expr)
        return result
    if isinstance(stmt, ast.ConditionalStatement):
        branches = [stmt.ifTrue, stmt.ifFalse]
    elif isinstance(stmt, (ast.CaseStatement, ast.PatternCaseStatement)):
        branches = [*(item.stmt for item in stmt.items), stmt.defaultCase]
    else:
        # A loop may execute zero times. Other unsupported statement forms add no guarantee.
        return {}
    return reduce(lambda a, b: {name: a[name] & b[name] for name in a.keys() & b.keys()},
                  map(definite_writes, branches))


def process_timing(member):
    kind = member.procedureKind.name
    timing = member.body.timing if isinstance(member.body, ast.TimedStatement) else None
    events = timing.events if isinstance(timing, ast.EventListControl) else [timing]
    edges = [e for e in events if isinstance(e, ast.SignalEventControl) and e.edge.name != "None_"]
    if edges and len(edges) == len(events) and kind in {"Always", "AlwaysFF"}:
        return "edge", edges
    if kind in {"AlwaysComb", "AlwaysLatch"} or (kind == "Always" and (
        isinstance(timing, ast.ImplicitEventControl) or isinstance(timing, (ast.SignalEventControl, ast.EventListControl))
    )):
        return "comb", []
    return "simulation", []


def describe_structure(members, locate, source_text):
    groups, storage, memories, logic = {}, {}, {}, []

    def occurrence(kind, label, bits, node, inputs, outputs):
        key = kind, label, bits
        tile = groups.setdefault(key, {"kind": kind, "label": label, "bits": bits, "detail": "",
                                       "inputs": set(), "outputs": set(), "occurrences": []})
        tile["inputs"].update(inputs)
        tile["outputs"].update(outputs)
        tile["occurrences"].append({"source": locate(node.sourceRange.start),
                                    "text": source_text(node)})

    for member in members:
        if isinstance(member, (ast.NetSymbol, ast.VariableSymbol)) and member.initializer is not None:
            logic.append({
                "kind": "Declaration initializer", "source": locate(member.location),
                "text": str(member.syntax).strip(), "inputs": signal_refs(member.initializer),
                "outputs": [member.hierarchicalPath],
            })
        if isinstance(member, ast.VariableSymbol):
            dtype = member.type.canonicalType
            dimensions = []
            while isinstance(dtype, ast.FixedSizeUnpackedArrayType):
                idx = dtype.range
                dimensions.append((idx.left, idx.right))
                dtype = dtype.elementType.canonicalType
            if dimensions and dtype.isIntegral:
                depth = 1
                for left, right in dimensions:
                    depth *= abs(left - right) + 1
                ranges = "".join(f"[{left}:{right}]" for left, right in dimensions)
                memories[member.hierarchicalPath] = {
                    "kind": "memory", "label": member.name, "bits": dtype.bitWidth,
                    "net": member.hierarchicalPath,
                    "detail": f"{depth} words × {dtype.bitWidth} bits · {ranges} · word {span(dtype) or '[0]'}",
                    "inputs": set(), "outputs": set(), "occurrences": [{"source": locate(member.location)}],
                    "access": set(),
                }
            continue

        if isinstance(member, ast.PrimitiveInstanceSymbol):
            outputs, inputs = set(), set()
            for port in member.portConnections:
                if isinstance(port, ast.AssignmentExpression):
                    outputs.update(symbol.hierarchicalPath for symbol, _ in targets(port.left))
                elif port is not None:
                    inputs.update(signal_refs(port))
            occurrence("gate", member.primitiveType.name.upper(), 1, member.syntax, inputs, outputs)
            logic.append({"kind": member.primitiveType.name.upper(), "source": locate(member.location),
                          "text": str(member.syntax).strip(), "inputs": sorted(inputs),
                          "outputs": sorted(outputs)})
            continue

        if isinstance(member, ast.ContinuousAssignSymbol):
            item, kind, timing, edges = member.assignment, "assign", "comb", []
        elif isinstance(member, ast.ProceduralBlockSymbol):
            item, kind = member.body, member.procedureKind.name
            timing, edges = process_timing(member)
        else:
            continue
        writes = assignments(item)
        destinations = defaultdict(list)
        for assignment in writes:
            for symbol, expr in targets(assignment.left):
                destinations[symbol.hierarchicalPath].append((symbol, expr))
        reads = set(access(item, writes))
        outputs = set(destinations)
        site = locate(member.location)
        logic.append({"kind": kind, "source": site, "text": (
            str(member.syntax).strip() if member.syntax is not None else source_text(item)
        ), "inputs": sorted(reads), "outputs": sorted(outputs)})
        for name, tile in memories.items():
            if name in reads:
                tile["outputs"].add(name)
                tile["access"].add("clocked read" if timing == "edge" else
                                   "combinational read" if timing == "comb" else "procedural read")
            if name in outputs:
                for assignment in writes:
                    lhs = {symbol.hierarchicalPath for symbol, _ in targets(assignment.left)}
                    if name in lhs:
                        tile["inputs"].update(set(signal_refs(assignment.left)) - lhs)
                        tile["inputs"].update(signal_refs(assignment.right))
                tile["access"].add("clocked write" if timing == "edge" else
                                   "initialization" if kind == "Initial" else "procedural write")
        if isinstance(member, ast.ProceduralBlockSymbol) and timing != "simulation":
            guaranteed = definite_writes(item) if timing == "comb" else {}
            for name, writes_to in destinations.items():
                symbol = writes_to[0][0]
                dtype = symbol.type
                if not dtype.isIntegral:
                    continue
                assigned = 0
                for _, expr in writes_to:
                    assigned |= coverage(symbol, expr) or (1 << dtype.bitWidth) - 1
                pending = assigned & ~guaranteed.get(name, 0)
                if not pending:
                    continue
                storage_kind = "flop" if timing == "edge" else "latch"
                event = ", ".join(source_text(edge) for edge in edges)
                if timing == "comb":
                    event = ("always_latch" if kind == "AlwaysLatch" else
                             "Possible latch · conditional write coverage")
                offset = min(dtype.fixedRange.left, dtype.fixedRange.right)
                indices = [offset + i for i in range(dtype.bitWidth) if (pending >> i) & 1]
                if pending.bit_count() == dtype.bitWidth:
                    indices_label = span(dtype)
                else:
                    ranges = []
                    for idx in indices:
                        if ranges and idx == ranges[-1][1] + 1:
                            ranges[-1][1] = idx
                        else:
                            ranges.append([idx, idx])
                    indices_label = ", ".join(f"[{a}..{b}]" if a != b else f"[{a}]" for a, b in ranges)
                key = storage_kind, name, event, pending
                tile = storage.setdefault(key, {"kind": storage_kind, "label": symbol.name,
                    "bits": pending.bit_count(), "detail": " · ".join(filter(None, [indices_label, event])),
                    "inputs": set(), "outputs": {name}, "occurrences": []})
                tile["inputs"].update(reads)
                tile["occurrences"].append({"source": site})

        if timing == "simulation":
            continue

        def operators(node, drivers=()):
            if isinstance(node, ast.ForLoopStatement):
                node.body.visit(lambda child: operators(child, drivers))
                return ast.VisitAction.Skip
            if isinstance(node, ast.AssignmentExpression):
                assigned = [symbol.hierarchicalPath for symbol, _ in targets(node.left)]
                node.right.visit(lambda child: operators(child, assigned))
                return ast.VisitAction.Skip
            if isinstance(node, ast.ConditionalExpression):
                occurrence("mux", "Conditional mux", node.type.bitWidth, node, signal_refs(node), drivers)
            elif isinstance(node, (ast.ConditionalStatement, ast.CaseStatement, ast.PatternCaseStatement)):
                branches = assignments(node)
                dest = {symbol.hierarchicalPath for expr in branches for symbol, _ in targets(expr.left)}
                if dest:
                    label = "If selection" if isinstance(node, ast.ConditionalStatement) else "Case selection"
                    if timing == "edge":
                        label = "Clocked " + label.lower()
                    occurrence("mux", label, None, node, access(node, branches), dest)
            elif isinstance(node, (ast.BinaryExpression, ast.UnaryExpression)):
                op = node.op.name
                if op in BITWISE:
                    occurrence("gate", BITWISE[op], node.type.bitWidth, node, signal_refs(node), drivers)
                elif op in OPERATORS:
                    occurrence("operator", OPERATORS[op], node.type.bitWidth, node, signal_refs(node), drivers)
        item.visit(operators)

    for tile in memories.values():
        modes = sorted(tile.pop("access"))
        if modes:
            tile["detail"] += " · " + ", ".join(modes)
    tiles = [*storage.values(), *memories.values(), *groups.values()]
    for tile in tiles:
        tile["inputs"], tile["outputs"] = sorted(tile["inputs"]), sorted(tile["outputs"])
    return tiles, logic
