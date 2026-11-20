"""Lower a Slang elaboration to executable processes and continuous drivers."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import partial
from pathlib import Path, PurePosixPath
import re

import pyslang
from pyslang import ast

from rtl_sim_engine import Delay, Event, Signal, SimulationError, Target, Wait, commit
from rtl_sim_logic import Logic, binary, case_match, concatenate, mux, unary

UNARY = frozenset(("Plus", "Minus", "BitwiseNot", "LogicalNot", "BitwiseAnd", "BitwiseNand",
                   "BitwiseOr", "BitwiseNor", "BitwiseXor", "BitwiseXnor"))
BINARY = frozenset(ast.BinaryOperator.__members__) - {"LogicalImplication", "LogicalEquivalence"}
VALUES = ("NamedValue", "HierarchicalValue")


def literal(value):
    text = value.toString(pyslang.LiteralBase.Binary, False).replace("_", "").lower()
    if text.startswith("-"):
        return Logic.integer(value.bitWidth, int(text, 2))
    return Logic.bits(text.rjust(value.bitWidth, "0"))


@dataclass(frozen=True, slots=True)
class Expression:
    read: object
    dependencies: frozenset = frozenset()
    signed: bool = False

    def number(self):
        return self.read().num(self.signed)


@dataclass(frozen=True, slots=True)
class LValue:
    targets: object
    dependencies: frozenset = frozenset()
    signals: frozenset = frozenset()

    def read(self):
        return concatenate(target.read() for target in self.targets())


def dependencies(expressions):
    return frozenset().union(*(item.dependencies for item in expressions))


def has_drive_strength(node):
    # The Slang Python binding does not expose the explicit strength enum.
    try:
        return any(value is not None for value in node.driveStrength)
    except TypeError:
        return True


@dataclass(frozen=True, slots=True)
class Clock:
    unit: int
    precision: int

    def duration(self, value):
        steps = Decimal(value) * self.unit / self.precision
        return int(steps.to_integral_value(rounding=ROUND_HALF_UP)) * self.precision

    def time(self, now):
        return (now + self.unit // 2) // self.unit


class LoopBreak(Exception):
    pass


class LoopContinue(Exception):
    pass


def accept_loop(body):
    try:
        yield from body()
    except LoopContinue:
        pass


class Model:
    def __init__(self, engine, manager, root, clocks):
        self.engine = engine
        self.manager = manager
        self.root = root
        self.clocks = clocks
        self.signals = {}
        self.visible = []
        self.initializers = []
        self.drivers = []
        self.processes = []

    def fail(self, node, reason):
        loc = node.sourceRange.start if hasattr(node, "sourceRange") else node.location
        if loc:
            loc = self.manager.getFullyExpandedLoc(loc)
            path = Path(self.manager.getFullPath(loc.buffer))
            line = self.manager.getLineNumber(loc)
            column = self.manager.getColumnNumber(loc)
            raise SimulationError(f"{path.relative_to(self.root) if path.is_relative_to(self.root) else path}"
                                  f":{line}:{column}: {reason}")
        raise SimulationError(reason)

    def width(self, dtype, node):
        width = dtype.bitWidth
        if not dtype.isIntegral or width < 1 or width > 4096:
            self.fail(node, f"Unsupported data type {dtype}. Use an integral value up to 4096 bits.")
        return width

    def signal(self, symbol):
        if symbol in self.signals:
            return self.signals[symbol]
        if symbol.kind.name not in ("Variable", "Net"):
            self.fail(symbol, f"Unsupported runtime symbol {symbol.kind.name} ({symbol.name}).")
        dtype = symbol.type.canonicalType
        bounds = None
        if dtype.kind.name == "FixedSizeUnpackedArrayType":
            bounds = (dtype.range.lower, dtype.range.upper)
            dtype = dtype.elementType.canonicalType
        width = self.width(dtype, symbol)
        net = symbol.kind.name == "Net"
        if net and (symbol.netType.netKind.name not in ("Wire", "Tri", "UWire") or symbol.delay is not None
                    or has_drive_strength(symbol) or bounds is not None):
            self.fail(symbol, "Unsupported net type, delay, or drive strength.")
        packed = dtype.getBitVectorRange() if width > 1 else None
        value = Logic.fill(width, "z" if net else "x" if dtype.isFourState else "0")
        signal = Signal(symbol.hierarchicalPath, width, value, net, not dtype.isFourState, bounds,
                        (packed.left, packed.right) if packed is not None else None)
        self.signals[symbol] = signal
        return signal

    def selection(self, expr, clock):
        """Return the bit offset and optional unpacked index captured when a select executes."""
        dtype = expr.value.type.canonicalType
        if not dtype.hasFixedRange:
            self.fail(expr, "Select requires a fixed-range vector or memory.")
        bounds = dtype.fixedRange
        right, descending = bounds.right, bounds.isDescending
        if expr.kind.name == "ElementSelect":
            selector = self.expression(expr.selector, clock)
            if dtype.kind.name == "FixedSizeUnpackedArrayType":
                return selector.number, selector.dependencies, True
            if not dtype.isIntegral:
                self.fail(expr, "This array select is outside the supported simulation subset.")
            stride = expr.type.bitWidth

            def bit_offset():
                index = selector.number()
                return None if index is None else (index - right if descending else right - index) * stride

            return bit_offset, selector.dependencies, False

        left = self.expression(expr.left, clock)
        last = self.expression(expr.right, clock)
        if not dtype.isIntegral or dtype.arrayElementType is not None and dtype.arrayElementType.bitWidth > 1:
            self.fail(expr, "Part selects require a single packed vector.")
        kind = expr.selectionKind.name

        def range_offset():
            start, end = left.number(), last.number()
            if start is None or end is None:
                return None
            if kind == "IndexedUp":
                end = start if descending else start + end - 1
            elif kind == "IndexedDown":
                end = start - end + 1 if descending else start
            return end - right if descending else right - end

        return range_offset, dependencies((left, last)), False

    def lvalue(self, expr, clock):
        kind = expr.kind.name
        width = self.width(expr.type, expr)
        if kind in VALUES:
            signal = self.signal(expr.symbol)
            return LValue(lambda: (Target(signal, width),), signals=frozenset((signal,)))
        if kind == "Concatenation":
            operands = [self.lvalue(item, clock) for item in expr.operands]
            return LValue(lambda: tuple(target for item in operands for target in item.targets()),
                          dependencies(operands), frozenset().union(*(item.signals for item in operands)))
        if kind in ("ElementSelect", "RangeSelect"):
            index, reads, unpacked = self.selection(expr, clock)
            if unpacked:
                if expr.value.kind.name not in VALUES:
                    self.fail(expr, "Only one unpacked memory dimension is supported.")
                signal = self.signal(expr.value.symbol)
                return LValue(lambda: (Target(signal, width, index()),), reads, frozenset((signal,)))
            parent = self.lvalue(expr.value, clock)

            def target():
                (base,) = parent.targets()
                offset = index()
                return (Target(base.signal, width, base.index, base.offset + offset)
                        if offset is not None else Target(None, width),)

            return LValue(target, parent.dependencies | reads, parent.signals)
        self.fail(expr, f"Unsupported assignment target {kind}.")

    def expression(self, expr, clock, prior=None):
        kind = expr.kind.name
        signed = expr.type.isSigned
        if kind in ("IntegerLiteral", "UnbasedUnsizedIntegerLiteral"):
            value = literal(expr.value)
            return Expression(lambda: value, signed=signed)
        if kind in VALUES:
            symbol = expr.symbol
            if symbol.kind.name in ("Parameter", "EnumValue"):
                value = symbol.value.value
                if not isinstance(value, pyslang.SVInt):
                    self.fail(expr, "Only integral parameters and enum values are supported in expressions.")
                constant = literal(value)
                return Expression(lambda: constant, signed=signed)
            value = self.lvalue(expr, clock)
            return Expression(value.read, value.signals | value.dependencies, signed)
        if kind == "LValueReference" and prior is not None:
            return prior
        if kind == "Conversion":
            inner = self.expression(expr.operand, clock, prior)
            width = self.width(expr.type, expr)
            two_state = not expr.type.isFourState
            return Expression(lambda: inner.read().fit(width, inner.signed, two_state),
                              inner.dependencies, signed)
        if kind in ("ElementSelect", "RangeSelect"):
            index, reads, unpacked = self.selection(expr, clock)
            if unpacked:
                value = self.lvalue(expr, clock)
                return Expression(value.read, value.signals | reads, signed)
            base = self.expression(expr.value, clock, prior)
            width = self.width(expr.type, expr)

            def select():
                offset = index()
                return Logic.fill(width, "x") if offset is None else base.read().extract(offset, width)

            return Expression(select, base.dependencies | reads, signed)
        if kind == "Concatenation":
            parts = [self.expression(item, clock, prior) for item in expr.operands]
            self.width(expr.type, expr)
            return Expression(lambda: concatenate(item.read() for item in parts), dependencies(parts), signed)
        if kind == "Replication":
            repetitions = self.expression(expr.count, clock).number()
            part = self.expression(expr.concat, clock, prior)
            self.width(expr.type, expr)
            return Expression(lambda: concatenate([part.read()] * repetitions), part.dependencies, signed)
        if kind == "UnaryOp" and expr.op.name in UNARY:
            operand = self.expression(expr.operand, clock, prior)
            op, width = expr.op.name, self.width(expr.type, expr)
            return Expression(lambda: unary(op, operand.read(), width), operand.dependencies, signed)
        if kind == "BinaryOp" and expr.op.name in BINARY:
            left = self.expression(expr.left, clock, prior)
            right = self.expression(expr.right, clock, prior)
            op, width = expr.op.name, self.width(expr.type, expr)
            return Expression(lambda: binary(op, left.read(), right.read(), width, left.signed, right.signed),
                              left.dependencies | right.dependencies, signed)
        if kind == "ConditionalOp":
            checks = self.conditions(expr, clock, prior)
            yes = self.expression(expr.left, clock, prior)
            no = self.expression(expr.right, clock, prior)

            def select():
                check = checks.read()
                state = check.truth()
                return mux(check, yes.read(), no.read()) if state is None else (yes if state else no).read()

            return Expression(select, dependencies((checks, yes, no)), signed)
        if kind == "Call" and expr.isSystemCall:
            method = expr.subroutineName
            if method in ("$signed", "$unsigned"):
                inner = self.expression(expr.arguments[0], clock, prior)
                return Expression(inner.read, inner.dependencies, method == "$signed")
            if method in ("$time", "$stime"):
                width = self.width(expr.type, expr)
                return Expression(lambda: Logic.integer(width, clock.time(self.engine.now)), signed=signed)
        self.fail(expr, f"Unsupported expression {kind}" +
                  (f" ({expr.subroutineName})" if kind == "Call" else
                   f" ({expr.op.name})" if kind in ("UnaryOp", "BinaryOp") else "") + ".")

    def conditions(self, stmt, clock, prior=None):
        if any(check.pattern is not None for check in stmt.conditions):
            self.fail(stmt, "Pattern matches in conditions are outside the supported subset.")
        checks = [self.expression(check.expr, clock, prior) for check in stmt.conditions]

        def value():
            unknown = False
            for check in checks:
                state = check.read().truth()
                if state == 0:
                    return Logic(1)
                unknown |= state is None
            return Logic.fill(1, "x" if unknown else "1")

        return Expression(value, dependencies(checks))

    def delay(self, timing, clock):
        if timing.kind.name != "Delay":
            self.fail(timing, f"Unsupported delay {timing.kind.name}.")
        expr = timing.expr
        if expr.kind.name in ("RealLiteral", "TimeLiteral"):
            duration = clock.duration(str(expr.value))
            return lambda: duration
        expr = self.expression(expr, clock)

        def duration():
            number = expr.number()
            return clock.duration(0 if number is None else number)

        return duration

    def event_wait(self, timing, clock):
        kind = timing.kind.name
        if kind == "EventList":
            children = [self.event_wait(child, clock) for child in timing.events]
            return Wait(tuple(event for child in children for event in child.events))
        if kind != "SignalEvent":
            self.fail(timing, f"Unsupported event control {kind}.")
        expression = self.expression(timing.expr, clock)
        condition = self.expression(timing.iffCondition, clock).read if timing.iffCondition is not None else None
        return Wait((Event(expression.read, expression.dependencies, timing.edge.name, condition),))

    def assignment(self, expr, clock):
        target = self.lvalue(expr.left, clock)
        captured = [()]
        prior = Expression(lambda: concatenate(item.read() for item in captured[0]), signed=expr.left.type.isSigned)
        value = self.expression(expr.right, clock, prior)
        width = self.width(expr.left.type, expr.left)
        delay = self.delay(expr.timingControl, clock) if expr.timingControl is not None else None
        nonblocking = expr.isNonBlocking

        def execute():
            if expr.isCompound or nonblocking or delay is None:
                captured[0] = target.targets()
            sample = value.read().fit(width, value.signed)
            if nonblocking:
                self.engine.schedule(partial(commit, self.engine, captured[0], sample),
                                     delay() if delay else 0, nba=True)
            else:
                if delay:
                    yield Delay(delay())
                    if not expr.isCompound:
                        captured[0] = target.targets()
                commit(self.engine, captured[0], sample)

        return execute

    def effect(self, expr, clock, scope):
        if expr.kind.name == "Assignment":
            return self.assignment(expr, clock)
        if expr.kind.name == "UnaryOp" and expr.op.name in (
                "Preincrement", "Postincrement", "Predecrement", "Postdecrement"):
            place = self.lvalue(expr.operand, clock)
            delta = -1 if "decrement" in expr.op.name else 1

            def increment():
                targets = place.targets()
                original = concatenate(target.read() for target in targets)
                result = binary("Add", original, Logic.integer(original.width, delta), original.width)
                commit(self.engine, targets, result)

            return self.action(increment)
        if expr.kind.name == "Call" and expr.isSystemCall:
            return self.system_task(expr, clock, scope)
        self.fail(expr, f"Unsupported procedural expression {expr.kind.name}.")

    @staticmethod
    def action(callback):
        def execute():
            callback()
            yield from ()
        return execute

    def sequence(self, statements):
        def execute():
            for stmt in statements:
                if self.engine.done:
                    return
                self.engine.charge()
                yield from stmt()
        return execute

    def statement(self, stmt, clock, scope):
        kind = stmt.kind.name
        sub = lambda node: self.statement(node, clock, scope)
        if kind == "Block":
            if stmt.blockKind.name != "Sequential":
                self.fail(stmt, "Parallel fork blocks are outside the supported simulation subset.")
            return sub(stmt.body)
        if kind == "List":
            return self.sequence([sub(item) for item in stmt.list])
        if kind in ("Empty", "VariableDeclaration"):
            if kind == "VariableDeclaration" and stmt.symbol.lifetime.name == "Automatic" and stmt.symbol.initializer is not None:
                signal = self.signal(stmt.symbol)
                value = self.expression(stmt.symbol.initializer, clock)
                return self.action(lambda: signal.write(self.engine, None, 0, value.read()))
            return self.action(lambda: None)
        if kind == "ExpressionStatement":
            return self.effect(stmt.expr, clock, scope)
        if kind == "Timed":
            body = sub(stmt.stmt)
            if stmt.timing.kind.name == "ImplicitEvent":
                wait = Wait(changes=self.sensitivity(stmt.stmt))
                def implicit():
                    yield wait
                    yield from body()
                return implicit
            if stmt.timing.kind.name == "Delay":
                duration = self.delay(stmt.timing, clock)
                def delayed():
                    yield Delay(duration())
                    yield from body()
                return delayed
            wait = self.event_wait(stmt.timing, clock)
            def timed():
                yield wait
                yield from body()
            return timed
        if kind == "Conditional":
            checks, yes = self.conditions(stmt, clock), sub(stmt.ifTrue)
            no = sub(stmt.ifFalse) if stmt.ifFalse is not None else self.action(lambda: None)
            def conditional():
                yield from (yes if checks.read().truth() == 1 else no)()
            return conditional
        if kind == "Case":
            style = stmt.condition.name
            if style == "Inside" or stmt.check.name != "None_":
                self.fail(stmt, "Case inside and unique, unique0, or priority checks are outside the supported subset.")
            test = self.expression(stmt.expr, clock)
            arms = [([self.expression(label, clock) for label in arm.expressions], sub(arm.stmt))
                    for arm in stmt.items]
            otherwise = sub(stmt.defaultCase) if stmt.defaultCase is not None else self.action(lambda: None)

            def select_case():
                actual = test.read()
                for labels, body in arms:
                    if any(case_match(actual, label.read(), style) for label in labels):
                        yield from body()
                        return
                yield from otherwise()
            return select_case
        if kind in ("ForLoop", "WhileLoop", "RepeatLoop", "ForeverLoop"):
            return self.loop(stmt, clock, scope)
        if kind == "Break":
            return self.action(lambda: self.jump(LoopBreak))
        if kind == "Continue":
            return self.action(lambda: self.jump(LoopContinue))
        if kind == "Wait":
            condition, body = self.expression(stmt.cond, clock), sub(stmt.stmt)
            def wait_until():
                while condition.read().truth() != 1:
                    yield Wait(changes=condition.dependencies)
                yield from body()
            return wait_until
        if kind == "ImmediateAssertion" and not stmt.isDeferred and stmt.assertionKind.name == "Assert":
            check = self.expression(stmt.cond, clock)
            yes = sub(stmt.ifTrue) if stmt.ifTrue is not None else self.action(lambda: None)
            no = sub(stmt.ifFalse) if stmt.ifFalse is not None else self.action(
                lambda: self.fail(stmt, "Immediate assertion failed."))
            def assertion():
                yield from (yes if check.read().truth() == 1 else no)()
            return assertion
        self.fail(stmt, f"Unsupported statement {kind}.")

    @staticmethod
    def jump(exception):
        raise exception

    def loop(self, stmt, clock, scope):
        body = self.statement(stmt.body, clock, scope)
        kind = stmt.kind.name
        always = lambda: True
        no_work = self.action(lambda: None)
        initialize, advance, again, repeat = no_work, no_work, always, None
        if kind == "ForLoop":
            variables = [self.initialize_variable(item, clock) for item in stmt.loopVars]
            initialize = self.sequence([*variables, *(self.effect(value, clock, scope)
                                                    for value in stmt.initializers)])
            advance = self.sequence([self.effect(value, clock, scope) for value in stmt.steps])
            if stmt.stopExpr is not None:
                condition = self.expression(stmt.stopExpr, clock)
                again = lambda: condition.read().truth() == 1
        elif kind == "WhileLoop":
            condition = self.expression(stmt.cond, clock)
            again = lambda: condition.read().truth() == 1
        elif kind == "RepeatLoop":
            repeat = self.expression(stmt.count, clock)

        def iterate():
            yield from initialize()
            remaining = 0 if repeat is None else max(repeat.number() or 0, 0)
            try:
                while not self.engine.done and (repeat is None or remaining > 0) and again():
                    self.engine.charge()
                    yield from accept_loop(body)
                    yield from advance()
                    remaining -= 1
            except LoopBreak:
                return
        return iterate

    def initialize_variable(self, symbol, clock):
        signal = self.signal(symbol)
        if symbol.initializer is None:
            return self.action(lambda: None)
        expr = self.expression(symbol.initializer, clock)
        return self.action(lambda: signal.write(self.engine, None, 0, expr.read().fit(signal.width, expr.signed)))

    def sensitivity(self, stmt, exclude_writes=False):
        reads, writes = set(), set()

        def inspect(expr):
            if isinstance(expr, ast.AssignmentExpression):
                def target(node):
                    if isinstance(node, ast.ElementSelectExpression):
                        node.selector.visit(inspect)
                        node.value.visit(target)
                        return ast.VisitAction.Skip
                    if isinstance(node, ast.RangeSelectExpression):
                        node.left.visit(inspect)
                        node.right.visit(inspect)
                        node.value.visit(target)
                        return ast.VisitAction.Skip
                    if isinstance(node, ast.ValueExpressionBase):
                        writes.add(node.symbol)
                        if expr.isCompound:
                            reads.add(node.symbol)
                expr.left.visit(target)
                expr.right.visit(inspect)
                return ast.VisitAction.Skip
            if isinstance(expr, ast.ValueExpressionBase) and expr.symbol.kind.name in ("Variable", "Net"):
                reads.add(expr.symbol)

        stmt.visit(inspect)
        return frozenset(self.signal(symbol) for symbol in (reads - writes if exclude_writes else reads))

    def register_scope(self, scope, clock):
        for member in scope:
            kind = member.kind.name
            if kind == "Instance":
                self.register_instance(member)
            elif kind in ("GenerateBlock", "GenerateBlockArray", "InstanceArray"):
                if not member.isUninstantiated:
                    self.register_scope(member, clock)
            elif kind in ("Variable", "Net"):
                signal = self.signal(member)
                self.visible.append(signal)
                if member.initializer is not None:
                    if signal.bounds is not None:
                        self.fail(member, "Whole-memory initialization is outside the supported subset.")
                    if signal.net:
                        value = self.expression(member.initializer, clock)
                        self.drivers.append((value, self.direct(signal)))
                    else:
                        self.initializers.append(self.initialize_variable(member, clock))
            elif kind == "ProceduralBlock":
                self.processes.append(self.process(member, clock, scope.hierarchicalPath))
            elif kind == "ContinuousAssign":
                if member.delay is not None or has_drive_strength(member):
                    self.fail(member, "Continuous assignment delays and drive strengths are outside the supported subset.")
                assignment = member.assignment
                self.drivers.append((self.expression(assignment.right, clock), self.lvalue(assignment.left, clock)))
            elif kind in ("PrimitiveInstance", "UninstantiatedDefinition", "SpecifyBlock",
                          "ClockingBlock", "ConcurrentAssertion", "CheckerInstance", "MultiPort", "InterfacePort"):
                self.fail(member, f"Unsupported design construct {kind}.")

    def register_instance(self, instance):
        if not instance.isModule:
            self.fail(instance, "Only module instances are supported.")
        clock = self.clocks[instance.hierarchicalPath]
        self.register_scope(instance.body, clock)
        for port in instance.body.portList:
            if port.kind.name != "Port" or port.direction.name not in ("In", "Out") or port.internalSymbol is None:
                self.fail(port, "Only integral input and output ports are supported.")
            signal = self.signal(port.internalSymbol)
            if signal.bounds is not None:
                self.fail(port, "Unpacked array ports are outside the supported subset.")
            connection = instance.getPortConnection(port)
            if connection is None or connection.expression is None or connection.expression.kind.name == "EmptyArgument":
                continue
            external = connection.expression
            source = Expression(signal.read, frozenset((signal,)), port.type.isSigned)
            parent = instance.hierarchicalPath.rpartition(".")[0]
            parent_clock = self.clocks.get(parent, clock)
            if port.direction.name == "In":
                self.drivers.append((self.expression(external, parent_clock), self.direct(signal)))
            elif external.kind.name != "Assignment":
                self.fail(port, "Unsupported output port connection.")
            elif external.left.kind.name != "EmptyArgument":
                self.drivers.append((source, self.lvalue(external.left, parent_clock)))

    @staticmethod
    def direct(signal):
        return LValue(lambda: (Target(signal, signal.width),), signals=frozenset((signal,)))

    def process(self, block, clock, scope):
        kind = block.procedureKind.name
        if kind not in ("Initial", "Always", "AlwaysFF", "AlwaysComb", "AlwaysLatch"):
            self.fail(block, f"Unsupported process {kind}.")
        body = self.statement(block.body, clock, scope)
        implicit = kind in ("AlwaysComb", "AlwaysLatch")
        wait = Wait(changes=self.sensitivity(block.body, exclude_writes=True)) if implicit else None

        def instructions():
            while not self.engine.done:
                self.engine.charge()
                yield from body()
                if kind == "Initial" or self.engine.done:
                    break
                if implicit:
                    yield wait
        return instructions()

    def start(self, top):
        self.register_instance(top)
        for initialize in self.initializers:
            next(initialize(), None)
        for process in self.processes:
            self.engine.add_process(process)
        for value, target in self.drivers:
            width = sum(item.width for item in target.targets())
            self.engine.drive(lambda value=value, width=width: value.read().fit(width, value.signed),
                              target.targets, value.dependencies | target.dependencies)

    def capture(self, names, depth=0):
        for name in names:
            matches = [signal for signal in self.visible if signal.path == name or
                       signal.path.startswith(name + ".") and
                       (not depth or signal.path[len(name) + 1:].count(".") < depth)]
            if not matches:
                raise SimulationError(f"No simulation signal or instance matched {name}.")
            self.engine.wave.add(matches)

    def read_memory(self, expr, clock):
        method, args = expr.subroutineName, expr.arguments
        filename, memory = args[:2]
        while filename.kind.name == "Conversion":
            filename = filename.operand
        if memory.kind.name == "Assignment":
            memory = memory.left
        if filename.kind.name != "StringLiteral" or memory.kind.name not in VALUES:
            self.fail(expr, f"{method} requires a literal filename and a memory name.")
        signal = self.signal(memory.symbol)
        if signal.bounds is None:
            self.fail(expr, f"{method} requires a fixed-size unpacked memory.")
        path = (self.root / filename.value).resolve()
        if not path.is_relative_to(self.root):
            self.fail(expr, "Memory data must be inside the project.")
        ranges = [self.expression(item, clock) for item in args[2:]]
        pattern = r"[01xz]+" if method == "$readmemb" else r"[0-9a-fxz]+"

        def load():
            lower, upper = signal.bounds
            start = ranges[0].number() if ranges else lower
            end = ranges[1].number() if len(ranges) == 2 else upper
            if start is None or end is None or not all(lower <= point <= upper for point in (start, end)):
                self.fail(expr, "Memory load range is outside the declared bounds.")
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    self.fail(expr, "Memory initialization data exceeds 2 MB.")
                source = path.read_text()
            except (OSError, UnicodeError) as error:
                self.fail(expr, f"Could not read memory data {filename.value}: {error}")
            contents = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", " ", source)
            step = 1 if start <= end else -1
            cursor = start
            for word in contents.lower().split():
                self.engine.charge()
                word = word.replace("_", "")
                if re.fullmatch(r"@[0-9a-f]+", word):
                    cursor = int(word[1:], 16)
                    continue
                if not re.fullmatch(pattern, word) or len(word) > 4096:
                    self.fail(expr, f"Invalid memory data in {filename.value}.")
                if not min(start, end) <= cursor <= max(start, end):
                    self.engine.print(f"WARNING {method} stopped at the configured memory bounds.")
                    break
                if method == "$readmemh":
                    word = "".join(digit * 4 if digit in "xz" else f"{int(digit, 16):04b}" for digit in word)
                signal.write(self.engine, cursor, 0, Logic.bits(word).fit(signal.width))
                cursor += step
        return self.action(load)

    def system_task(self, expr, clock, scope):
        method, args = expr.subroutineName, expr.arguments
        if method in ("$display", "$error", "$warning", "$fatal", "$info"):
            if method == "$fatal" and args and args[0].kind.name != "StringLiteral":
                args = args[1:]
            message = self.message(expr, args, clock, scope)

            def write():
                text = message()
                if method == "$fatal":
                    self.fail(expr, text or "Testbench called $fatal.")
                self.engine.print((f"{method[1:].upper()} " if method != "$display" else "") + text)
                if method == "$error":
                    self.engine.test_errors += 1
            return self.action(write)
        if method in ("$finish", "$stop"):
            return self.action(lambda: self.engine.finish(f"Testbench called {method}"))
        if method in ("$readmemh", "$readmemb"):
            return self.read_memory(expr, clock)
        if method == "$dumpfile":
            name = args[0]
            while name.kind.name == "Conversion":
                name = name.operand
            if name.kind.name != "StringLiteral":
                self.fail(expr, "$dumpfile expects a literal filename.")
            filename = PurePosixPath(name.value).name
            return self.action(lambda: setattr(self.engine.wave, "filename", filename or "simulation.vcd"))
        if method == "$dumpvars":
            depth = self.expression(args[0], clock) if args else None
            picks = []
            for arg in args[1:]:
                if arg.kind.name in (*VALUES, "ArbitrarySymbol"):
                    picks.append(arg.symbol.hierarchicalPath)
                else:
                    self.fail(arg, "$dumpvars supports signal and scope names.")

            def dump():
                levels = depth.number() if depth is not None else 0
                self.capture(picks or [scope], levels)
            return self.action(dump)
        self.fail(expr, f"Unsupported system task {method}.")

    def message(self, call, args, clock, scope):
        def argument(item):
            if item.kind.name == "StringLiteral":
                return lambda: item.value
            expr = self.expression(item, clock)
            return lambda: (expr.read(), expr.signed)

        if not args or args[0].kind.name != "StringLiteral":
            values = [argument(item) for item in args]
            return lambda: " ".join(render(value(), "d") for value in values)
        template = args[0].value
        directive = re.compile(r"%(\d*)([bBoOdDhHxXtcCsSm%])")
        matches = list(directive.finditer(template))
        if "%" in directive.sub("", template) or any(len(match[1]) > 4 or int(match[1] or "0") > 4096
                                                       for match in matches):
            self.fail(call, "Unsupported display format specifier.")
        expected = sum(match[2] not in ("%", "m") for match in matches)
        if expected > len(args) - 1:
            self.fail(call, "Display format is missing arguments.")
        readers = [argument(item) for item in args[1:]]

        def formatted():
            values = iter(read() for read in readers)

            def substitute(match):
                width, code = match.groups()
                if code == "%":
                    return "%"
                if code == "m":
                    return scope
                value = next(values)
                if code == "t" and not isinstance(value, str):
                    number = value[0].num(value[1])
                    text = "x" if number is None else str(number * clock.unit // self.engine.wave.tick)
                else:
                    text = render(value, code.lower(), compact=width == "0")
                if not width and code.lower() == "d" and isinstance(value, tuple):
                    # Default decimal width is enough to hold the whole integral argument.
                    bits, signed = value
                    width = str(len(str(1 << (bits.width - int(signed)))) + int(signed))
                return text.rjust(int(width)) if width else text

            output = directive.sub(substitute, template)
            extra = list(values)
            return output + "".join(" " + render(value, "d") for value in extra)

        return formatted


def render(value, base, compact=False):
    if isinstance(value, str):
        return value
    logic, signed = value
    if base == "d":
        number = logic.num(signed)
        return str(number) if number is not None else "z" if logic.zs == (1 << logic.width) - 1 else "x"
    if base in ("s", "c"):
        return (logic.ones.to_bytes((logic.width + 7) // 8, "big").lstrip(b"\0").decode("latin1")
                if base == "s" else chr(logic.ones & 255))
    if base == "b":
        text = logic.text()
    else:
        chunk = 3 if base == "o" else 4
        pieces = []
        for pos in range(0, logic.width, chunk):
            part = logic.extract(pos, min(chunk, logic.width - pos))
            pieces.append("z" if part.zs == (1 << part.width) - 1 else
                          "x" if part.unknown else format(part.ones, "x"))
        text = "".join(reversed(pieces))
    return text.lstrip("0") or "0" if compact else text
