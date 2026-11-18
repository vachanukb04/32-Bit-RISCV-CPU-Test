"""Lower supported elaborated RTL expressions to connected schematic symbols."""

from contextlib import contextmanager

from pyslang import SVInt, ast

from rtl_case_regions import CASE_NAMES, case_label

from rtl_process_view import expression_label, unconverted
from rtl_structure import BITWISE, OPERATORS, VALUE_EXPRESSIONS, access, assignments, definite_writes, integer_literal, process_timing, signal_refs, span, targets


GATE_OPERATORS = {
    "BinaryAnd": "&", "BinaryOr": "|", "BinaryXor": "^", "BinaryXnor": "~^",
    "BitwiseNot": "~", "BitwiseAnd": "&", "BitwiseOr": "|", "BitwiseXor": "^",
    "BitwiseNand": "~&", "BitwiseNor": "~|", "BitwiseXnor": "~^",
    "LogicalAnd": "&&", "LogicalOr": "||", "LogicalNot": "!",
}


OPERATOR_MARKS = {
    "Add": "+", "Subtract": "−", "Multiply": "×", "Divide": "÷", "Mod": "%",
    "Power": "**", "Minus": "−", "Equality": "==", "Inequality": "!=",
    "CaseEquality": "===", "CaseInequality": "!==", "LessThan": "<", "GreaterThan": ">",
    "LessThanEqual": "<=", "GreaterThanEqual": ">=", "LogicalShiftLeft": "<<",
    "LogicalShiftRight": ">>", "ArithmeticShiftLeft": "<<<", "ArithmeticShiftRight": ">>>",
}


CASE_OPERATORS = {"Normal": ("Case equality", "==="), "WildcardXOrZ": ("Casex match", "?="),
                  "WildcardJustZ": ("Casez match", "?=")}


def level_control(expr):
    """Return a one-bit input and its active polarity for a direct level test."""
    expr = unconverted(expr)
    low = False
    if isinstance(expr, ast.UnaryExpression) and expr.op.name in {"LogicalNot", "BitwiseNot"}:
        expr = unconverted(expr.operand)
        low = True
    elif isinstance(expr, ast.BinaryExpression) and expr.op.name in {"Equality", "Inequality"}:
        for operand, comparison in [(expr.left, expr.right), (expr.right, expr.left)]:
            value = integer_literal(comparison)
            signal = unconverted(operand)
            if value in (0, 1) and isinstance(signal, VALUE_EXPRESSIONS) and signal.type.bitWidth == 1:
                return signal, (value == 0) != (expr.op.name == "Inequality")
    if isinstance(expr, VALUE_EXPRESSIONS) and expr.type.bitWidth == 1:
        return expr, low
    return None


def single_statement(stmt):
    while isinstance(stmt, (ast.TimedStatement, ast.BlockStatement, ast.StatementList)):
        if isinstance(stmt, ast.TimedStatement):
            stmt = stmt.stmt
        elif isinstance(stmt, ast.BlockStatement):
            stmt = stmt.body
        elif len(stmt.list) == 1:
            stmt = stmt.list[0]
        else:
            break
    return stmt


def value_name(node, text):
    while isinstance(node, ast.ConversionExpression):
        node = node.operand
    if isinstance(node, VALUE_EXPRESSIONS):
        return node.symbol.name
    return text(node)


class Drawing:
    def __init__(self, text, resolver=None, cases=None, destination=None):
        self.text = text
        self.resolver = resolver
        self.nodes = []
        self.cases = cases
        self.case_region = None
        self.destination = destination

    @contextmanager
    def in_region(self, region):
        previous, self.case_region = self.case_region, region
        try:
            yield
        finally:
            self.case_region = previous

    def arm_value(self, region, driver, label=None):
        label = label or self.destination
        if not region or not label:
            return driver
        return self.add("tap", label, [("IN", driver)], caseRegion=region,
                        bits=self.nodes[driver].get("bits"), traceThrough=True)

    def add(self, kind, label, inputs=(), **fields):
        pins = [{"pin": pin, "source": source} for pin, source in inputs]
        index = len(self.nodes)
        shape = {"kind": kind, "label": label, "pins": pins, **fields}
        if self.case_region:
            shape.setdefault("caseRegion", self.case_region)
        self.nodes.append(shape)
        return index

    def terminal(self, label, nets=(), **fields):
        return self.add("terminal", label, nets=list(nets), **fields)

    def output(self, label, driver, nets, **fields):
        return self.add("output", label, [("IN", driver)], nets=list(nets), **fields)

    def expression(self, node):
        if self.cases:
            with self.in_region(self.cases.owner(node)):
                return self.lower_expression(node)
        return self.lower_expression(node)

    def lower_expression(self, node):
        if self.resolver:
            resolved = self.resolver(node)
            if resolved is not None:
                return resolved
        if isinstance(node, ast.ConversionExpression):
            operand = self.expression(node.operand)
            if isinstance(node.operand, ast.IntegerLiteral) or (
                node.type.bitWidth == node.operand.type.bitWidth and node.type.isSigned == node.operand.type.isSigned
            ):
                return operand
            label = f"{node.type.bitWidth}-bit {'signed' if node.type.isSigned else 'unsigned'}"
            return self.add("operator", label, [("A", operand)], bits=node.type.bitWidth)
        if isinstance(node, (ast.ConcatenationExpression, ast.ReplicationExpression)) and node.type.isIntegral:
            operands = node.operands if isinstance(node, ast.ConcatenationExpression) else [node.concat]
            label = "Concatenate" if isinstance(node, ast.ConcatenationExpression) else "Replicate"
            width = node.type.bitWidth
            cursor = width
            fields = []
            pins = []
            for operand in operands:
                field_width = operand.type.bitWidth if label == "Concatenate" else width
                if not field_width:
                    continue
                high = cursor - 1
                cursor -= field_width
                selector = f"[{high}:{cursor}]" if high != cursor else f"[{cursor}]"
                field = expression_label(operand, self.text) if label == "Concatenate" else expression_label(node, self.text)
                # A single operand inside a repetition is already represented by its own wire.
                source = operand
                bare = unconverted(operand)
                if isinstance(bare, ast.ReplicationExpression):
                    group = bare.concat
                    if isinstance(group, ast.ConcatenationExpression) and len(group.operands) == 1:
                        source = group.operands[0]
                elif isinstance(node, ast.ReplicationExpression) and len(operands) == 1:
                    if isinstance(source, ast.ConcatenationExpression) and len(source.operands) == 1:
                        source = source.operands[0]
                fields.append({"range": selector, "text": field})
                pins.append((str(len(pins)), self.expression(source)))
            if fields:
                return self.add("bitfields", label, pins, fields=fields, bits=width)
        if isinstance(node, ast.ConditionalExpression) and len(node.conditions) == 1:
            condition = node.conditions[0]
            if condition.pattern is None:
                return self.mux(self.expression(node.right), self.expression(node.left),
                                self.expression(condition.expr), bits=node.type.bitWidth)
        if isinstance(node, (ast.BinaryExpression, ast.UnaryExpression)):
            operation = node.op.name
            operands = [node.left, node.right] if isinstance(node, ast.BinaryExpression) else [node.operand]
            if operation in BITWISE or operation in OPERATORS:
                kind = "gate" if operation in BITWISE else "operator"
                return self.add(kind, (BITWISE | OPERATORS)[operation],
                                zip(("A", "B"), map(self.expression, operands)),
                                bits=node.type.bitWidth, symbol=(GATE_OPERATORS if kind == "gate" else OPERATOR_MARKS).get(operation))
        if isinstance(node, ast.ElementSelectExpression):
            symbol = node.value.getSymbolReference()
            if symbol is not None and symbol.type.isUnpackedArray:
                return self.add("memory", symbol.name, [("ADDR", self.expression(node.selector))],
                                nets=[symbol.hierarchicalPath])
        refs = signal_refs(node)
        label = value_name(node, self.text) or node.kind.name
        if len(refs) > 1:
            # Preserve each dependency for expressions represented by their RTL text.
            return self.add("expression", label, [
                (str(i + 1), self.terminal(ref.rsplit(".", 1)[-1], [ref]))
                for i, ref in enumerate(refs)
            ])
        if node.type.isIntegral:
            fields = {"bits": node.type.bitWidth}
            if isinstance(node, VALUE_EXPRESSIONS):
                bounds = node.type.fixedRange
                if abs(bounds.left - bounds.right) + 1 == node.type.bitWidth and node.type.bitWidth > 1:
                    fields["range"] = str(bounds)
            return self.terminal(label, refs, **fields)
        return self.terminal(label, refs)

    def mux(self, when_zero, when_one, select, **fields):
        return self.add("mux", "MUX", [("0", when_zero), ("1", when_one), ("S", select)], **fields)

    def combine(self, gates, label):
        if not gates:
            return self.terminal("1'b0" if label == "OR" else "1'b1", bits=1)
        if len(gates) == 1:
            return gates[0]
        middle = len(gates) // 2
        return self.add("gate", label, [("A", self.combine(gates[:middle], label)),
                                        ("B", self.combine(gates[middle:], label))], bits=1)

    def next_value(self, statement, name, current):
        """Lower a procedural assignment tree for one full-width target."""
        if isinstance(statement, ast.TimedStatement):
            return self.next_value(statement.stmt, name, current)
        if isinstance(statement, ast.BlockStatement):
            return self.next_value(statement.body, name, current)
        if isinstance(statement, ast.StatementList):
            for item in statement.list:
                current = self.next_value(item, name, current)
            return current
        if isinstance(statement, ast.ExpressionStatement):
            item = statement.expr
            if not isinstance(item, ast.AssignmentExpression):
                raise ValueError("unsupported procedural expression")
            if not isinstance(item.left, VALUE_EXPRESSIONS) or item.isCompound or item.timingControl is not None:
                raise ValueError("unsupported procedural write")
            if item.left.symbol.hierarchicalPath == name:
                return self.expression(item.right)
            return current
        if isinstance(statement, ast.ConditionalStatement) and len(statement.conditions) == 1:
            condition = statement.conditions[0]
            if condition.pattern is None:
                zero = self.next_value(statement.ifFalse, name, current)
                one = self.next_value(statement.ifTrue, name, current)
                if zero == one:
                    return zero
                region = self.cases.owner(statement) if self.cases else None
                with self.in_region(region):
                    return self.mux(zero, one, self.expression(condition.expr))
        if isinstance(statement, ast.CaseStatement) and statement.condition.name in CASE_OPERATORS:
            label, symbol = CASE_OPERATORS[statement.condition.name]
            selector = self.expression(statement.expr)
            fallback = self.next_value(statement.defaultCase, name, current)
            case = self.cases.case(statement) if self.cases else None
            if self.cases and statement.defaultCase is not None and fallback != current:
                fallback = self.arm_value(self.cases.arm(statement, len(statement.items)), fallback)
            for index, arm in reversed(list(enumerate(statement.items))):
                branch = self.next_value(arm.stmt, name, current)
                if branch == fallback:
                    continue
                region = self.cases.arm(statement, index) if self.cases else None
                with self.in_region(region):
                    matches = []
                    for item in arm.expressions:
                        operand = self.expression(item)
                        label_text = case_label(item, expression_label(item, self.text))
                        if label_text != expression_label(item, self.text):
                            operand = self.terminal(label_text)
                        matches.append(self.add("operator", label, [("A", selector), ("B", operand)],
                                                symbol=symbol, bits=1))
                    condition = self.combine(matches, "OR")
                    alternate = self.arm_value(region, branch)
                match = ", ".join(expression_label(item, self.text) for item in arm.expressions)
                mode = CASE_NAMES[statement.condition.name]
                otherwise = "No match" if index == len(statement.items) - 1 else "Next case arm"
                with self.in_region(case):
                    fallback = self.mux(fallback, alternate, condition, branch=f"{mode} {match}", fallback=otherwise)
            return fallback
        if statement is None or statement.kind.name == "Empty":
            return current
        raise ValueError("unsupported procedural control")

    def finish(self, title, source, root):
        # Branch lowering can create dead intermediate nodes after a later assignment.
        used = set()
        def mark(index):
            if index not in used:
                used.add(index)
                for pin in self.nodes[index]["pins"]:
                    mark(pin["source"])
        mark(root)
        ids = {old: new for new, old in enumerate(sorted(used))}
        nodes = [self.nodes[index] for index in sorted(used)]
        for node in nodes:
            for pin in node["pins"]:
                pin["source"] = ids[pin["source"]]
        return {"title": title, "source": source, "nodes": nodes}


def data_schematics(members, structures, locate, text, case_graphs):
    schematics = []
    for member in members:
        if isinstance(member, (ast.NetSymbol, ast.VariableSymbol)) and member.initializer is not None:
            drawing = Drawing(text)
            data = drawing.expression(member.initializer)
            sink = drawing.output(member.name, data, [member.hierarchicalPath])
            sheet = drawing.finish(member.name, locate(member.location), sink)
            if isinstance(member, ast.VariableSymbol):
                sheet.update(expressionOnly=True, processKind="Initial")
            schematics.append(sheet)
            continue
        if isinstance(member, ast.PrimitiveInstanceSymbol):
            gate = member.primitiveType.name.upper()
            ports = member.portConnections
            if (gate in {"AND", "NAND", "OR", "NOR", "XOR", "XNOR", "NOT", "BUF"}
                    and len(ports) == (2 if gate in {"NOT", "BUF"} else 3)
                    and isinstance(ports[0], ast.AssignmentExpression)):
                drawing = Drawing(text)
                pin_names = ("A", "B")
                cell = drawing.add("gate", gate, zip(pin_names, map(drawing.expression, ports[1:])))
                lhs = value_name(ports[0].left, text)
                sink = drawing.output(lhs, cell, [sym.hierarchicalPath for sym, _ in targets(ports[0].left)])
                schematics.append(drawing.finish(lhs, locate(member.location), sink))
            continue
        if isinstance(member, ast.ContinuousAssignSymbol):
            item = member.assignment
            lhs = text(item.left) or value_name(item.left, text)
            drawing = Drawing(text)
            driver = drawing.expression(item.right)
            sink = drawing.output(lhs, driver, [sym.hierarchicalPath for sym, _ in targets(item.left)])
            schematics.append(drawing.finish(lhs, locate(member.location), sink))
            continue
        if not isinstance(member, ast.ProceduralBlockSymbol):
            continue
        timing, edges = process_timing(member)
        writes = assignments(member.body)
        if not writes:
            continue
        dests = {symbol.hierarchicalPath: symbol for item in writes for symbol, _ in targets(item.left)}
        source = locate(member.location)
        cases = case_graphs.get((source["file"], source["line"], source["column"]))
        complete = []
        if timing == "edge":
            complete = clocked_schematics(member, edges, writes, dests, source, text, cases)
        elif timing == "comb" and len(dests) == 1:
            sheet = process_schematic(member, writes, dests, structures, source, text, cases)
            if sheet is not None:
                complete = [sheet]
        if complete:
            schematics.extend(complete)
            continue
        for item in writes:
            lhs = list(targets(item.left))
            if not lhs:
                continue
            symbol, selected = lhs[0]
            site = locate(item.sourceRange.start)
            target = text(item.left) or symbol.name
            title = f"{target} · value at L{site['line']}"
            drawing = Drawing(text, cases=cases)
            data = drawing.expression(item.right)
            if cases:
                data = drawing.arm_value(cases.owner(item), data, target)
            if len(lhs) == 1 and isinstance(selected, ast.ElementSelectExpression) and symbol.type.isUnpackedArray:
                # These are the actual operands of one write, with the guard left in the RTL.
                pins = [("ADDR", drawing.expression(selected.selector)), ("WD", data)]
                if len(edges) == 1:
                    pins.append(("CLK", drawing.terminal(value_name(edges[0].expr, text), signal_refs(edges[0].expr))))
                sink = drawing.add("memory", symbol.name, pins, write=True, nets=[symbol.hierarchicalPath])
                title = f"{symbol.name} · write at L{site['line']}"
            else:
                sink = drawing.output(target, data, sorted({sym.hierarchicalPath for sym, _ in lhs}))
            sheet = drawing.finish(title, site, sink)
            sheet["expressionOnly"] = True
            sheet["processKind"] = member.procedureKind.name
            sheet["processSource"] = source
            schematics.append(sheet)
    return sorted(schematics, key=lambda sheet: sheet.get("processKind") == "Initial")


def clock_controls(member, edges):
    if not 1 <= len(edges) <= 2:
        return None
    body = member.body
    reset, reset_branch = None, None
    clock = edges[0]
    if len(edges) == 2:
        branch = single_statement(body)
        if (not isinstance(branch, ast.ConditionalStatement) or len(branch.conditions) != 1 or
                branch.conditions[0].pattern is not None):
            return None
        control = level_control(branch.conditions[0].expr)
        if control is None:
            return None
        signal, low = control
        reset_edge = "NegEdge" if low else "PosEdge"
        reset = next((edge for edge in edges if edge.edge.name == reset_edge and
                      isinstance(unconverted(edge.expr), VALUE_EXPRESSIONS) and
                      unconverted(edge.expr).symbol.hierarchicalPath == signal.symbol.hierarchicalPath), None)
        if reset is None:
            return None
        clock = next(edge for edge in edges if edge is not reset)
        if signal.symbol.hierarchicalPath in signal_refs(clock.expr):
            return None
        reset_branch, body = branch.ifTrue, branch.ifFalse

    # Only lower edge events without an additional event guard.
    if any(getattr(event, "iffCondition", None) is not None for event in edges):
        return None
    return clock, reset, reset_branch, body


def clocked_schematics(member, edges, writes, dests, source, text, cases):
    if (any(not isinstance(item.left, VALUE_EXPRESSIONS) for item in writes) or
            (len(writes) > 1 and any(not item.isNonBlocking for item in writes))):
        return []
    controls = clock_controls(member, edges)
    if controls is None:
        return []
    clock, reset, reset_branch, body = controls
    reset_writes = assignments(reset_branch) if reset is not None else []
    reset_coverage = definite_writes(reset_branch) if reset is not None else {}
    schematics = []
    for name, symbol in dests.items():
        if not symbol.type.isIntegral:
            return []
        width = symbol.type.bitWidth
        drawing = Drawing(text, cases=cases, destination=symbol.name)
        feedback = drawing.terminal(symbol.name + " (Q feedback)", [name], bits=width)
        try:
            data = drawing.next_value(body, name, feedback)
        except ValueError:
            return []
        fields = {"edge": clock.edge.name, "bits": width}
        pins = [("D", data), ("CLK", drawing.expression(clock.expr))]
        if reset is not None:
            values = [item.right for item in reset_writes
                      if isinstance(item.left, VALUE_EXPRESSIONS) and item.left.symbol.hierarchicalPath == name]
            if len(values) != 1 or reset_coverage.get(name) != (1 << width) - 1:
                return []
            constant = values[0].eval(ast.EvalContext(symbol)).value
            if not isinstance(constant, SVInt) or constant.hasUnknown:
                return []
            pins.append(("ARST", drawing.expression(reset.expr)))
            fields.update(resetLow=reset.edge.name == "NegEdge", resetValue=str(constant))
        label = symbol.name + span(symbol.type)
        flop = drawing.add("flop", label, pins, **fields)
        sink = drawing.output(label, flop, [name])
        schematics.append(drawing.finish(symbol.name, source, sink))
    return schematics


def process_schematic(member, writes, dests, structures, source, text, cases):
    name, symbol = next(iter(dests.items()))
    if not symbol.type.isIntegral or any(not isinstance(item.left, VALUE_EXPRESSIONS) for item in writes):
        return None
    drawing = Drawing(text, cases=cases, destination=symbol.name)
    latch = any(s["kind"] == "latch" and name in s["outputs"] and
                any(site["source"] == source for site in s["occurrences"]) for s in structures)
    if latch:
        branch = single_statement(member.body)
        if (isinstance(branch, ast.ConditionalStatement) and branch.ifFalse is None
                and len(branch.conditions) == 1 and branch.conditions[0].pattern is None
                and len(writes) == 1):
            condition = branch.conditions[0].expr
            control, low = level_control(condition) or (condition, False)
            data = drawing.expression(writes[0].right)
            gate = drawing.expression(control)
            node = drawing.add("latch", symbol.name + span(symbol.type), [("D", data), ("EN", gate)],
                               enableLow=low, bits=symbol.type.bitWidth)
            sink = drawing.output(symbol.name + span(symbol.type), node, [name])
            return drawing.finish(symbol.name, source, sink)
        return None
    if name in access(member.body, writes):
        return None
    initial = drawing.terminal(symbol.name, [name])
    try:
        data = drawing.next_value(member.body, name, initial)
    except ValueError:
        return None
    sink = drawing.output(symbol.name + span(symbol.type), data, [name])
    return drawing.finish(symbol.name, source, sink)


def clock_routes(members, children, logic, locate, text):
    """Record RTL clock consumers and lift their input bindings across instances."""
    uses = []
    continuous_drivers = {}
    aliases = {}
    for member in members:
        if isinstance(member, ast.ContinuousAssignSymbol):
            item = member.assignment
            if isinstance(item.left, VALUE_EXPRESSIONS):
                signal = item.left.symbol.hierarchicalPath
                continuous_drivers[signal] = value_name(item.right, text)
                if isinstance(item.right, VALUE_EXPRESSIONS):
                    aliases[signal] = item.right.symbol.hierarchicalPath

    def traced_refs(refs):
        reached = set(refs)
        for ref in refs:
            while ref in aliases and aliases[ref] not in reached:
                ref = aliases[ref]
                reached.add(ref)
        return sorted(reached)

    for member in members:
        if not isinstance(member, ast.ProceduralBlockSymbol):
            continue
        timing, edges = process_timing(member)
        if timing != "edge":
            continue
        dests = {symbol.name for item in assignments(member.body) for symbol, _ in targets(item.left)}
        controls = clock_controls(member, edges)
        for event in edges:
            uses.append({"signal": value_name(event.expr, text), "refs": signal_refs(event.expr),
                         "label": ", ".join(sorted(dests)) or "Process", "edge": event.edge.name,
                         "pin": "CLK" if controls is not None and event is controls[0] else "Edge event",
                         "source": locate(event.sourceRange.start)})
    for child in children:
        for port in child["ports"]:
            if port["direction"] not in {"input", "inout"}:
                continue
            reached = [use for use in child["clockUses"] if port["internal"] in use["traceRefs"]]
            clocks = [use for use in reached if use["pin"] in {"CLK", "Clock input"}]
            if not reached:
                continue
            edges = {use["edge"] for use in reached}
            uses.append({"signal": port["binding"] or "(unconnected)", "refs": port["refs"],
                         "label": child["name"] + "." + port["name"],
                         "pin": "Clock input" if clocks else "Edge event input",
                         "edge": next(iter(edges)) if len(edges) == 1 else "",
                         "source": port["bindingSource"] or child["source"]})
    for use in uses:
        use["traceRefs"] = traced_refs(use["refs"])
        use["driverExpressions"] = [continuous_drivers[ref] for ref in use["refs"] if ref in continuous_drivers]
        use["driverPorts"] = [
            child["name"] + "." + port["name"]
            for child in children for port in child["ports"]
            if port["direction"] == "output" and set(use["refs"]).intersection(port["drives"])
        ]
        use["drivers"] = [item["source"] for item in logic
                          if set(use["refs"]).intersection(item["outputs"])]
    return uses
