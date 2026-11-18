"""Source regions for elaborated case statements and their arms."""

from pyslang import SVInt, ast

from rtl_process_view import expression_label, unconverted
from rtl_structure import VALUE_EXPRESSIONS


CASE_NAMES = {"Normal": "case", "WildcardJustZ": "casez", "WildcardXOrZ": "casex", "Inside": "case inside"}


def case_label(expr, label):
    expr = unconverted(expr)
    symbol = expr.symbol if isinstance(expr, VALUE_EXPRESSIONS) else None
    if isinstance(symbol, ast.ParameterSymbol):
        val = symbol.value.value
        bits = symbol.type.bitWidth
        if isinstance(val, SVInt) and not val.hasUnknown and 0 < bits <= 64:
            encoded = int(val) & ((1 << bits) - 1)
            return f"{label} ({bits}'b{encoded:0{bits}b})"
    return label


def first_location(node):
    start = node.sourceRange.start
    return (start.buffer.id, start.offset) if start else None


class CaseRegions:
    def __init__(self, body, locate, text):
        self.regions = []
        self.cases = {}
        self.arms = []
        self.text = text
        self.locate = locate
        body.visit(self.collect)

    def owner(self, node):
        start = first_location(node)
        if start is not None:
            buffer, offset = start
            # The AST visits an enclosing arm before its nested cases.
            for source, first, last, key in reversed(self.arms):
                if buffer == source and first <= offset < last:
                    return key
        return None

    def collect(self, node):
        if not isinstance(node, (ast.CaseStatement, ast.PatternCaseStatement)):
            return
        position = first_location(node)
        if position is None:
            return
        name = CASE_NAMES.get(node.condition.name, "case")
        selector = self.text(node.expr) or expression_label(node.expr, self.text)
        selector = " ".join(selector.split())
        if isinstance(node, ast.PatternCaseStatement):
            label = f"case ({selector}) matches"
        elif name == "case inside":
            label = f"case ({selector}) inside"
        else:
            label = f"{name} ({selector})"
        key = f"case-{len(self.cases)}"
        self.cases[position] = key
        source = self.locate(node.sourceRange.start)
        self.regions.append({"key": key, "parent": self.owner(node), "kind": "case", "label": label, "source": source})
        for index, arm in enumerate(node.items):
            if isinstance(node, ast.PatternCaseStatement):
                pattern = " ".join(self.text(arm.pattern).split()) or "Pattern"
                if arm.filter is not None:
                    pattern += " &&& " + expression_label(arm.filter, self.text)
                labels = [pattern]
            else:
                labels = [case_label(item, " ".join(self.text(unconverted(item)).split()) or
                                     expression_label(item, self.text)) for item in arm.expressions]
            self.add_arm(node, arm.stmt, index, ", ".join(labels))
        if node.defaultCase is not None:
            self.add_arm(node, node.defaultCase, len(node.items), "default")

    def add_arm(self, node, body, index, label):
        key = self.arm(node, index)
        start, end = body.sourceRange.start, body.sourceRange.end
        if start and end and start.buffer == end.buffer:
            self.arms.append((start.buffer.id, start.offset, end.offset, key))
        self.regions.append({
            "key": key, "parent": self.case(node), "kind": "arm", "label": label, "color": index,
            "source": self.locate(start or node.sourceRange.start),
            "empty": isinstance(body, ast.EmptyStatement),
        })

    def case(self, node):
        return self.cases.get(first_location(node))

    def arm(self, node, index):
        key = self.case(node)
        return f"{key}-arm-{index}" if key else None
