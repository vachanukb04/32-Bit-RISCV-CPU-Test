"""Lower supported elaborated RTL expressions to connected schematic symbols."""

from pyslang import ast

from rtl_process_view import expression_label, unconverted
from rtl_structure import BITWISE, OPERATORS, VALUE_EXPRESSIONS, access, assignments, process_timing, signal_refs, span, targets


def value_name(node, text):
    while isinstance(node, ast.ConversionExpression):
        node = node.operand
    if isinstance(node, VALUE_EXPRESSIONS):
        return node.symbol.name
    return text(node)


class Drawing:
    def __init__(self, text):
        self.text = text
        self.nodes = []

    def add(self, kind, label, inputs=(), **fields):
        pins = [{"pin": pin, "source": source} for pin, source in inputs]
        index = len(self.nodes)
        self.nodes.append({"kind": kind, "label": label, "pins": pins, **fields})
        return index

    def terminal(self, label, nets=()):
        return self.add("terminal", label, nets=list(nets))

    def output(self, label, driver, nets):
        return self.add("output", label, [("IN", driver)], nets=list(nets))

    def expression(self, node):
        if isinstance(node, ast.ConversionExpression):
            operand = self.expression(node.operand)
            if isinstance(node.operand, ast.IntegerLiteral) or (
                node.type.bitWidth == node.operand.type.bitWidth and node.type.isSigned == node.operand.type.isSigned
            ):
                return operand
            label = f"{node.type.bitWidth}-bit {'signed' if node.type.isSigned else 'unsigned'}"
            return self.add("operator", label, [("A", operand)])
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
                                self.expression(condition.expr))
        if isinstance(node, (ast.BinaryExpression, ast.UnaryExpression)):
            operation = node.op.name
            operands = [node.left, node.right] if isinstance(node, ast.BinaryExpression) else [node.operand]
            if operation in BITWISE or operation in OPERATORS:
                kind = "gate" if operation in BITWISE else "operator"
                return self.add(kind, (BITWISE | OPERATORS)[operation],
                                zip(("A", "B"), map(self.expression, operands)))
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
        return self.terminal(label, refs)

    def mux(self, when_zero, when_one, select):
        return self.add("mux", "MUX", [("0", when_zero), ("1", when_one), ("S", select)])

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
            lhs = list(targets(item.left))
            if len(lhs) == 1 and lhs[0][0].hierarchicalPath == name:
                if isinstance(item.left, VALUE_EXPRESSIONS) and not item.isCompound:
                    return self.expression(item.right)
                raise ValueError("partial procedural write")
            raise ValueError("multiple procedural targets")
        if isinstance(statement, ast.ConditionalStatement) and len(statement.conditions) == 1:
            condition = statement.conditions[0]
            if condition.pattern is None:
                zero = self.next_value(statement.ifFalse, name, current)
                one = self.next_value(statement.ifTrue, name, current)
                if zero == one:
                    return zero
                return self.mux(zero, one, self.expression(condition.expr))
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


def data_schematics(members, structures, locate, text):
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
        complete = (process_schematic(member, timing, edges, writes, dests, structures, source, text)
                    if timing in {"edge", "comb"} else None)
        if complete is not None:
            schematics.append(complete)
            continue
        for item in writes:
            lhs = list(targets(item.left))
            if not lhs:
                continue
            symbol, selected = lhs[0]
            site = locate(item.sourceRange.start)
            target = text(item.left) or symbol.name
            title = f"{target} · value at L{site['line']}"
            drawing = Drawing(text)
            data = drawing.expression(item.right)
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


def process_schematic(member, timing, edges, writes, dests, structures, source, text):
    # A full data path is limited to one destination whose updates can be ordered.
    if len(dests) != 1:
        return None
    name, symbol = next(iter(dests.items()))
    if not symbol.type.isIntegral or any(not isinstance(item.left, VALUE_EXPRESSIONS) for item in writes):
        return None
    drawing = Drawing(text)
    kind = "flop" if timing == "edge" else "logic"
    latch = next((s for s in structures if s["kind"] == "latch" and name in s["outputs"]), None)
    if latch:
        kind = "latch"
    if kind == "latch":
        branch = member.body
        wrappers = (ast.TimedStatement, ast.BlockStatement)
        while isinstance(branch, wrappers):
            branch = branch.stmt if isinstance(branch, ast.TimedStatement) else branch.body
        if (isinstance(branch, ast.ConditionalStatement) and branch.ifFalse is None
                and len(branch.conditions) == 1 and branch.conditions[0].pattern is None
                and len(writes) == 1):
            gate = drawing.expression(branch.conditions[0].expr)
            data = drawing.expression(writes[0].right)
            latch_node = drawing.add("latch", symbol.name + span(symbol.type), [("D", data), ("EN", gate)])
            sink = drawing.output(symbol.name + span(symbol.type), latch_node, [name])
            return drawing.finish(symbol.name, source, sink)
        return None
    if len(edges) > 1 or any(not item.isNonBlocking for item in writes) and timing == "edge":
        return None
    if kind == "logic" and name in access(member.body, writes):
        return None
    # Q feedback is a named terminal to keep the schematic acyclic.
    initial = drawing.terminal(symbol.name + (" (Q feedback)" if kind == "flop" else ""), [name])
    try:
        data = drawing.next_value(member.body, name, initial)
    except ValueError:
        return None
    if kind == "flop":
        clock = drawing.terminal(value_name(edges[0].expr, text), signal_refs(edges[0].expr))
        data = drawing.add("flop", symbol.name + span(symbol.type), [("D", data), ("CLK", clock)],
                           edge=edges[0].edge.name)
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
        # More than one event can include asynchronous reset or set. Keep event roles explicit.
        for event in edges:
            uses.append({"signal": value_name(event.expr, text), "refs": signal_refs(event.expr),
                         "label": ", ".join(sorted(dests)) or "Process", "edge": event.edge.name,
                         "pin": "CLK" if len(edges) == 1 else "Edge event",
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
