"""Build source-linked decision and initialization views from elaborated statements."""

from pyslang import SVInt, ast

from rtl_structure import VALUE_EXPRESSIONS, integer_literal, signal_refs, targets


def unconverted(expr):
    while isinstance(expr, ast.ConversionExpression):
        expr = expr.operand
    return expr


def expression_label(expr, text):
    expr = unconverted(expr)
    if isinstance(expr, ast.ConcatenationExpression):
        return " │ ".join(expression_label(field, text) for field in expr.operands)
    if isinstance(expr, ast.ReplicationExpression):
        operand = expression_label(expr.concat, text)
        if isinstance(expr.concat, ast.ConcatenationExpression) and len(expr.concat.operands) > 1:
            operand = f"({operand})"
        return f"{text(expr.count).strip()} × {operand}"
    if isinstance(expr, ast.ConditionalExpression) and len(expr.conditions) == 1:
        condition = expr.conditions[0]
        if condition.pattern is None:
            return (f"{expression_label(condition.expr, text)} ? "
                    f"{expression_label(expr.left, text)} : {expression_label(expr.right, text)}")
    return " ".join(text(expr).split()) or getattr(getattr(expr, "symbol", None), "name", expr.kind.name)


def statements(stmt):
    """Flatten lexical wrappers while preserving execution order."""
    if stmt is None or stmt.kind.name == "Empty":
        return
    if isinstance(stmt, ast.BlockStatement):
        yield from statements(stmt.body)
    elif isinstance(stmt, ast.StatementList):
        for part in stmt.list:
            yield from statements(part)
    else:
        yield stmt


def row(kind, text, depth, stmt, locate):
    return {"kind": kind, "text": text, "depth": depth,
            "source": locate(stmt.sourceRange.start) if stmt.sourceRange.start else None}


def decision_rows(stmt, depth, locate, text):
    for part in statements(stmt):
        if isinstance(part, ast.TimedStatement):
            yield from decision_rows(part.stmt, depth, locate, text)
        elif isinstance(part, ast.ConditionalStatement) and all(c.pattern is None for c in part.conditions):
            label = " && ".join(expression_label(c.expr, text) for c in part.conditions)
            yield row("if", label, depth, part, locate)
            yield from decision_rows(part.ifTrue, depth + 1, locate, text)
            if part.ifFalse is not None:
                yield row("else", "", depth, part.ifFalse, locate)
                yield from decision_rows(part.ifFalse, depth + 1, locate, text)
        elif isinstance(part, ast.CaseStatement):
            modes = {"WildcardXOrZ": "case x", "WildcardJustZ": "case z", "Inside": "case inside"}
            yield row(modes.get(part.condition.name, "case"), expression_label(part.expr, text), depth, part, locate)
            for arm in part.items:
                label = ", ".join(expression_label(expr, text) for expr in arm.expressions)
                yield row("match", label, depth + 1, arm.stmt, locate)
                yield from decision_rows(arm.stmt, depth + 2, locate, text)
            if part.defaultCase is not None:
                yield row("default", "", depth + 1, part.defaultCase, locate)
                yield from decision_rows(part.defaultCase, depth + 2, locate, text)
        elif isinstance(part, ast.ExpressionStatement) and isinstance(part.expr, ast.AssignmentExpression):
            item = part.expr
            lhs = expression_label(item.left, text)
            rhs = expression_label(item.right, text)
            operator = "←" if not item.isCompound else (item.op.name + " ←")
            yield row("assign", f"{lhs} {operator} {rhs}", depth, part, locate)
        else:
            yield row("rtl", " ".join(text(part).split()) or part.kind.name, depth, part, locate)


def index_path(expr):
    expr = unconverted(expr)
    if isinstance(expr, VALUE_EXPRESSIONS):
        return expr.symbol.hierarchicalPath
    return None


def constant_integer(expr, symbol):
    if expr is None:
        return None
    value = integer_literal(expr)
    if value is not None or signal_refs(expr):
        return value
    value = expr.eval(ast.EvalContext(symbol)).value
    return int(value) if isinstance(value, SVInt) and not value.hasUnknown else None


def step_amount(expr, symbol):
    index = symbol.hierarchicalPath
    if isinstance(expr, ast.UnaryExpression) and index_path(expr.operand) == index:
        return {"Preincrement": 1, "Postincrement": 1, "Predecrement": -1,
                "Postdecrement": -1}.get(expr.op.name)
    if not isinstance(expr, ast.AssignmentExpression) or expr.isCompound or index_path(expr.left) != index:
        return None
    rhs = unconverted(expr.right)
    if isinstance(rhs, ast.BinaryExpression) and index_path(rhs.left) == index:
        amount = constant_integer(rhs.right, symbol)
        if amount is not None:
            return {"Add": amount, "Subtract": -amount}.get(rhs.op.name)
    return None


def counted_sweep(loop):
    if len(loop.steps) != 1 or not isinstance(loop.stopExpr, ast.BinaryExpression):
        return None
    if len(loop.initializers) == 1:
        init = loop.initializers[0]
        if (not isinstance(init, ast.AssignmentExpression) or init.isCompound or init.isNonBlocking or
                not isinstance(init.left, VALUE_EXPRESSIONS)):
            return None
        symbol = init.left.symbol
        start = constant_integer(init.right, symbol)
    elif len(loop.loopVars) == 1:
        symbol = loop.loopVars[0]
        start = constant_integer(symbol.initializer, symbol)
    else:
        return None
    index = symbol.hierarchicalPath
    test = loop.stopExpr
    bound = constant_integer(test.right, symbol)
    step = step_amount(loop.steps[0], symbol)
    if start is None or bound is None or not step or index_path(test.left) != index:
        return None
    op = test.op.name
    limit = {"LessThan": bound - 1, "LessThanEqual": bound}.get(op) if step > 0 else (
        {"GreaterThan": bound + 1, "GreaterThanEqual": bound}.get(op))
    if limit is None or (limit - start) * step < 0:
        return None
    last = start + ((limit - start) // step) * step
    end = last + step
    for dtype in (symbol.type, test.left.type):
        bits = dtype.bitWidth
        if not dtype.isIntegral or bits < 1:
            return None
        low = -(1 << (bits - 1)) if dtype.isSigned else 0
        high = (1 << (bits - int(dtype.isSigned))) - 1
        if not all(low <= value <= high for value in (start, last, end)):
            return None
    return symbol, start, last, step, end


def known_assignment(stmt):
    item = stmt.expr if isinstance(stmt, ast.ExpressionStatement) else None
    if (not isinstance(item, ast.AssignmentExpression) or item.isCompound or item.isNonBlocking or
            item.timingControl is not None):
        return None
    symbol = item.left.getSymbolReference()
    return item if symbol is not None and constant_integer(item.right, symbol) is not None else None


def initialization_view(member, locate, text):
    rows, outputs, indices, values = [], {}, {}, []

    def record(target, item, part):
        values.append({"target": target, "value": expression_label(item.right, text),
                       "bits": item.right.type.bitWidth, "source": locate(part.sourceRange.start)})

    for stmt in statements(member.body):
        if isinstance(stmt, ast.ForLoopStatement):
            sweep = counted_sweep(stmt)
            if sweep is None:
                return None
            index, start, last, step, final = sweep
            body = list(statements(stmt.body))
            if not body:
                return None
            fill_rows = []
            for part in body:
                item = known_assignment(part)
                lhs = item.left if item is not None else None
                if not isinstance(lhs, ast.ElementSelectExpression) or index_path(lhs.selector) != index.hierarchicalPath:
                    return None
                target = lhs.value.getSymbolReference()
                if target is None or not isinstance(target.type.canonicalType, ast.FixedSizeUnpackedArrayType):
                    return None
                bounds = target.type.canonicalType.range
                if any(not min(bounds.left, bounds.right) <= point <= max(bounds.left, bounds.right)
                       for point in (start, last)):
                    return None
                net = target.hierarchicalPath
                outputs[net] = target.name
                address = f"{start}..{last}" if step == 1 else index.name
                marker = f"{target.name}[{address}]"
                record(marker, item, part)
                fill_rows.append(row("fill", f"{marker} ← {expression_label(item.right, text)}",
                                     1, part, locate))
            if not stmt.loopVars:
                indices[index.hierarchicalPath] = {"name": index.name, "start": start, "last": last,
                                                   "step": step, "final": final}
                outputs[index.hierarchicalPath] = f"{index.name} = {final}"
            rows.append(row("sweep", f"{index.name} · {start} → {last} · step {step:+d}", 0, stmt, locate))
            rows.extend(fill_rows)
            continue
        item = known_assignment(stmt)
        if item is None:
            return None
        lhs = item.left
        if not isinstance(lhs, VALUE_EXPRESSIONS) and not (
            isinstance(lhs, ast.ElementSelectExpression) and lhs.value.type.isUnpackedArray and
            integer_literal(lhs.selector) is not None
        ):
            return None
        if isinstance(lhs, ast.ElementSelectExpression):
            dtype = lhs.value.type.canonicalType
            if not isinstance(dtype, ast.FixedSizeUnpackedArrayType):
                return None
            address = integer_literal(lhs.selector)
            if not min(dtype.range.left, dtype.range.right) <= address <= max(dtype.range.left, dtype.range.right):
                return None
        for target, _ in targets(lhs):
            outputs[target.hierarchicalPath] = target.name
            indices.pop(target.hierarchicalPath, None)
        marker = expression_label(lhs, text)
        record(marker, item, stmt)
        rows.append(row("set", f"{marker} ← {expression_label(item.right, text)}", 0, stmt, locate))
    if not rows:
        return None
    return {"kind": "initialization", "rows": rows, "outputs": outputs, "indices": indices, "values": values}


def procedure_local_reads(body):
    """Find signals set by leading blocking assignments before any prior value is used."""
    while isinstance(body, ast.TimedStatement):
        body = body.stmt
    written, external = set(), set()
    for stmt in statements(body):
        item = stmt.expr if isinstance(stmt, ast.ExpressionStatement) else None
        if (not isinstance(item, ast.AssignmentExpression) or item.isCompound or item.isNonBlocking or
                item.timingControl is not None or not isinstance(item.left, VALUE_EXPRESSIONS)):
            break
        external.update(set(signal_refs(item.right)) - written)
        written.add(item.left.symbol.hierarchicalPath)
    return sorted(written - external)


def process_view(member, timing, locate, text):
    if member.procedureKind.name == "Initial":
        known = initialization_view(member, locate, text)
        if known:
            return known
        return {"kind": "initial", "rows": list(decision_rows(member.body, 0, locate, text))}
    if timing == "comb":
        return {"kind": "combinational", "rows": list(decision_rows(member.body, 0, locate, text)),
                "localReads": procedure_local_reads(member.body)}
    return None
