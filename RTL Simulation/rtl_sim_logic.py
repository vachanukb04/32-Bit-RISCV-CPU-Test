"""Four-state vectors for the RTL2GDS simulator."""

from dataclasses import dataclass
import operator


def mask(width):
    return (1 << width) - 1


@dataclass(frozen=True, slots=True)
class Logic:
    width: int
    ones: int = 0
    xs: int = 0
    zs: int = 0

    @classmethod
    def integer(cls, width, number):
        return cls(width, number & mask(width))

    @classmethod
    def bits(cls, text):
        bits = text.lower()
        return cls(len(bits), *(int("".join("1" if bit == state else "0" for bit in bits), 2)
                                 for state in "1xz"))

    @classmethod
    def fill(cls, width, bit):
        return (cls(width, xs=mask(width)) if bit == "x" else
                cls(width, zs=mask(width)) if bit == "z" else
                cls(width, ones=mask(width) if bit == "1" else 0))

    @property
    def unknown(self):
        return self.xs | self.zs

    def text(self):
        if not self.unknown:
            return format(self.ones, f"0{self.width}b")
        return "".join(self.bit(index) for index in range(self.width - 1, -1, -1))

    def bit(self, index):
        flag = 1 << index
        if self.xs & flag:
            return "x"
        if self.zs & flag:
            return "z"
        return "1" if self.ones & flag else "0"

    def num(self, signed=False):
        if self.unknown:
            return None
        if signed and self.ones >> (self.width - 1):
            return self.ones - (1 << self.width)
        return self.ones

    def truth(self):
        return 1 if self.ones else None if self.unknown else 0

    def fit(self, width, signed=False, two_state=False):
        if width > self.width and signed:
            return Logic.fill(width - self.width, self.bit(self.width - 1)).join(self).fit(
                width, two_state=two_state)
        bound = mask(width)
        return Logic(width, self.ones & bound, 0 if two_state else self.xs & bound,
                     0 if two_state else self.zs & bound)

    def join(self, other):
        return Logic(self.width + other.width,
                     (self.ones << other.width) | other.ones,
                     (self.xs << other.width) | other.xs,
                     (self.zs << other.width) | other.zs)

    def extract(self, start, width):
        begin, end = max(start, 0), min(start + width, self.width)
        if begin >= end:
            return Logic.fill(width, "x")
        bound = mask(end - begin)
        offset = begin - start
        valid = bound << offset
        return Logic(width, ((self.ones >> begin) & bound) << offset,
                     (mask(width) ^ valid) | (((self.xs >> begin) & bound) << offset),
                     ((self.zs >> begin) & bound) << offset)

    def replace(self, start, other):
        begin, end = max(start, 0), min(start + other.width, self.width)
        if begin >= end:
            return self
        new = other.extract(begin - start, end - begin)
        keep = mask(self.width) ^ (mask(end - begin) << begin)
        return Logic(self.width, (self.ones & keep) | (new.ones << begin),
                     (self.xs & keep) | (new.xs << begin), (self.zs & keep) | (new.zs << begin))


def truth_value(value):
    return Logic.fill(1, "x") if value is None else Logic.integer(1, value)


def concatenate(values):
    result = Logic(0)
    for value in values:
        result = result.join(value)
    return result


def mux(condition, yes, no):
    state = condition.truth()
    if state is not None:
        return yes if state else no
    ones, zs = yes.ones & no.ones, yes.zs & no.zs
    equal_zeros = mask(yes.width) & ~(yes.ones | no.ones | yes.unknown | no.unknown)
    return Logic(yes.width, ones, mask(yes.width) ^ (ones | zs | equal_zeros), zs)


def case_match(left, right, kind="Normal"):
    ignore = left.zs | right.zs if kind == "WildcardJustZ" else 0
    if kind == "WildcardXOrZ":
        ignore = left.unknown | right.unknown
    difference = (left.ones ^ right.ones) | (left.xs ^ right.xs) | (left.zs ^ right.zs)
    return not (difference & ~ignore)


def invert(value):
    return Logic(value.width, mask(value.width) ^ (value.ones | value.unknown), value.unknown)


def unary(op, value, width):
    if op == "Plus":
        return value.fit(width)
    if op == "Minus":
        return Logic.fill(width, "x") if value.unknown else Logic.integer(width, -value.ones)
    if op == "BitwiseNot":
        return invert(value)
    if op == "LogicalNot":
        state = value.truth()
        return truth_value(None if state is None else 1 - state)
    if op in ("BitwiseAnd", "BitwiseNand"):
        state = (0 if value.ones | value.unknown != mask(value.width) else
                 None if value.unknown else 1)
    elif op in ("BitwiseOr", "BitwiseNor"):
        state = value.truth()
    else:
        state = None if value.unknown else value.ones.bit_count() & 1
    if op in ("BitwiseNand", "BitwiseNor", "BitwiseXnor") and state is not None:
        state = 1 - state
    return truth_value(state)


ARITHMETIC = {"Add": operator.add, "Subtract": operator.sub, "Multiply": operator.mul}
RELATIONAL = {"GreaterThan": operator.gt, "GreaterThanEqual": operator.ge,
              "LessThan": operator.lt, "LessThanEqual": operator.le}


def binary(op, a, b, width, left_signed=False, right_signed=False):
    if op in ("LogicalAnd", "LogicalOr"):
        x, y = a.truth(), b.truth()
        decisive = int(op == "LogicalOr")
        return truth_value(decisive if decisive in (x, y) else
                           None if None in (x, y) else 1 - decisive)
    if op in ("CaseEquality", "CaseInequality"):
        return truth_value(case_match(a, b) == (op == "CaseEquality"))
    if op in ("Equality", "Inequality", "WildcardEquality", "WildcardInequality"):
        ignore = b.unknown if op.startswith("Wildcard") else 0
        unknown = (a.unknown | b.unknown) & ~ignore
        if (a.ones ^ b.ones) & ~(a.unknown | b.unknown):
            return truth_value(op in ("Inequality", "WildcardInequality"))
        return truth_value(None if unknown else op in ("Equality", "WildcardEquality"))
    if op.startswith("Binary"):
        zeros_a = mask(width) & ~(a.ones | a.unknown)
        zeros_b = mask(width) & ~(b.ones | b.unknown)
        if op == "BinaryAnd":
            ones, zeros = a.ones & b.ones, zeros_a | zeros_b
        elif op == "BinaryOr":
            ones, zeros = a.ones | b.ones, zeros_a & zeros_b
        else:
            uncertain = a.unknown | b.unknown
            ones = (a.ones ^ b.ones) & ~uncertain
            zeros = mask(width) ^ (ones | uncertain)
            if op == "BinaryXnor":
                ones, zeros = zeros, ones
        return Logic(width, ones, mask(width) ^ (ones | zeros))
    if op.endswith(("ShiftLeft", "ShiftRight")):
        amount = b.num()
        if amount is None:
            return Logic.fill(width, "x")
        amount = min(amount, width)
        if op.endswith("ShiftLeft"):
            return Logic(width, a.ones << amount, a.xs << amount, a.zs << amount).fit(width)
        fill = a.bit(a.width - 1) if op == "ArithmeticShiftRight" and left_signed else "0"
        retained = a.extract(amount, width - amount) if amount < width else Logic(0)
        return Logic.fill(amount, fill).join(retained)
    a_num = a.num(left_signed)
    b_num = b.num(right_signed)
    if a_num is None or b_num is None:
        return Logic.fill(width, "x")
    if op in ARITHMETIC:
        return Logic.integer(width, ARITHMETIC[op](a_num, b_num))
    if op in RELATIONAL:
        return truth_value(RELATIONAL[op](a_num, b_num))
    if op in ("Divide", "Mod"):
        if not b_num:
            return Logic.fill(width, "x")
        quotient = abs(a_num) // abs(b_num)
        if (a_num < 0) != (b_num < 0):
            quotient = -quotient
        return Logic.integer(width, quotient if op == "Divide" else a_num - quotient * b_num)
    if op == "Power":
        if b_num < 0:
            if a_num in (-1, 1):
                return Logic.integer(width, -1 if a_num == -1 and b_num & 1 else 1)
            return Logic.fill(width, "x") if a_num == 0 else Logic.integer(width, 0)
        return Logic.integer(width, pow(a_num, b_num, 1 << width))
    raise ValueError(f"Unsupported binary operator {op}")


def resolve(values, width):
    ones = zeros = xs = 0
    for value in values:
        ones |= value.ones
        zeros |= mask(width) & ~(value.ones | value.unknown)
        xs |= value.xs
    xs |= ones & zeros
    return Logic(width, ones & ~xs, xs, mask(width) & ~(ones | zeros | xs))
