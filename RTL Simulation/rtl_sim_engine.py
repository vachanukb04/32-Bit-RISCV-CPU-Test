"""Event queues, signal storage, and VCD output for the cockpit simulator."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import count

from rtl_sim_logic import Logic, resolve


TIME_UNITS = {"s": 10**15, "ms": 10**12, "us": 10**9, "ns": 10**6,
              "ps": 10**3, "fs": 1}


class SimulationError(ValueError):
    """An input or runtime error that can be reported at the cockpit boundary."""


def timescale(tick):
    for unit, base in TIME_UNITS.items():
        if tick >= base:
            return f"{tick // base}{unit}"


@dataclass(eq=False, slots=True)
class Signal:
    path: str
    width: int
    initial: Logic
    net: bool = False
    two_state: bool = False
    bounds: tuple | None = None
    packed_range: tuple | None = None
    value: Logic = field(init=False)
    words: dict = field(default_factory=dict)
    drivers: dict = field(default_factory=dict)
    listeners: dict = field(default_factory=dict)

    def __post_init__(self):
        self.value = self.initial

    def valid_word(self, index):
        return index is not None and self.bounds[0] <= index <= self.bounds[1]

    def read(self, index=None):
        if self.bounds is None:
            return self.value
        if self.valid_word(index):
            return self.words.get(index, self.initial)
        return Logic.fill(self.width, "x")

    def write(self, engine, index, offset, value, driver=None):
        if self.bounds is not None:
            if not self.valid_word(index):
                return
            previous = self.read(index)
            new = previous.replace(offset, value).fit(self.width, two_state=self.two_state)
            if new != previous:
                self.words[index] = new
                self.changed(engine)
            return
        if self.net:
            if driver is None:
                raise SimulationError(f"{self.path} requires a continuous driver.")
            original = self.drivers.get(driver, self.initial)
            driven = original.replace(offset, value)
            if driven == original:
                return
            self.drivers[driver] = driven
            new = resolve(self.drivers.values(), self.width)
        else:
            new = self.value.replace(offset, value).fit(self.width, two_state=self.two_state)
        if new != self.value:
            self.value = new
            self.changed(engine)

    def changed(self, engine):
        engine.wave.changed(self)
        for callback in tuple(self.listeners):
            callback()


@dataclass(frozen=True, slots=True)
class Target:
    signal: Signal | None
    width: int
    index: int | None = None
    offset: int = 0

    def read(self):
        return (self.signal.read(self.index).extract(self.offset, self.width) if self.signal
                else Logic.fill(self.width, "x"))

    def store(self, engine, value, driver=None):
        if self.signal is not None:
            self.signal.write(engine, self.index, self.offset, value.fit(self.width), driver)


def commit(engine, targets, value, driver=None):
    position = sum(target.width for target in targets)
    for target in targets:
        position -= target.width
        target.store(engine, value.extract(position, target.width), driver)


@dataclass(frozen=True, slots=True)
class Delay:
    duration: int
    nba: bool = False


@dataclass(frozen=True, slots=True)
class Event:
    read: object
    deps: frozenset
    edge: str = "None_"
    condition: object = None

    def matches(self, previous, current):
        if previous == current:
            return False
        if self.condition is not None and self.condition().truth() != 1:
            return False
        if self.edge == "None_":
            return True
        before, after = previous.bit(0), current.bit(0)
        if before == after:
            return False
        positive = before == "0" or after == "1"
        negative = before == "1" or after == "0"
        return (positive if self.edge == "PosEdge" else negative if self.edge == "NegEdge"
                else positive or negative)


@dataclass(frozen=True, slots=True)
class Wait:
    events: tuple = ()
    changes: frozenset = frozenset()


class Process:
    def __init__(self, engine, instructions):
        self.engine = engine
        self.instructions = iter(instructions)

    def advance(self):
        request = next(self.instructions, None)
        if isinstance(request, Delay):
            self.engine.schedule(self.advance, request.duration, request.nba)
        elif isinstance(request, Wait):
            self.wait(request)

    def wait(self, request):
        previous = [event.read() for event in request.events]
        watched = request.changes.union(*(event.deps for event in request.events))

        def wake():
            for signal in watched:
                signal.listeners.pop(changed, None)
            self.engine.active.append(self.advance)

        def changed():
            if request.changes:
                wake()
                return
            triggered = False
            for index, event in enumerate(request.events):
                current = event.read()
                triggered |= event.matches(previous[index], current)
                previous[index] = current
            if triggered:
                wake()

        for signal in watched:
            signal.listeners[changed] = None


class Wave:
    def __init__(self, tick):
        self.tick = tick
        self.filename = "simulation.vcd"
        self.ids = {}
        self.dirty = {}
        self.previous = {}
        self.lines = []
        self.bytes = 0

    def add(self, signals):
        for signal in signals:
            if signal in self.ids or signal.bounds is not None:
                continue
            if len(self.ids) >= 300:
                raise SimulationError("The waveform exceeds 300 signals. Reduce the $dumpvars scope.")
            code = len(self.ids)
            identifier = chr(33 + code % 94) + (chr(33 + code // 94) if code >= 94 else "")
            self.ids[signal] = identifier
            self.dirty[signal] = None

    def changed(self, signal):
        if signal in self.ids:
            self.dirty[signal] = None

    def append(self, line):
        self.bytes += len(line) + 1
        if self.bytes > 4 * 1024 * 1024:
            raise SimulationError("The waveform exceeds 4 MB. Shorten the run or reduce the $dumpvars scope.")
        self.lines.append(line)

    def flush(self, now):
        changes = []
        for signal in self.dirty:
            value = signal.read()
            if self.previous.get(signal) == value:
                continue
            self.previous[signal] = value
            text, code = value.text(), self.ids[signal]
            changes.append(text + code if value.width == 1 else f"b{text} {code}")
        self.dirty.clear()
        if changes:
            self.append(f"#{now // self.tick}")
            for line in changes:
                self.append(line)

    def export(self, now):
        if not self.ids:
            return None
        header = [f"$timescale {timescale(self.tick)} $end"]
        opened = []
        for signal, code in sorted(self.ids.items(), key=lambda item: item[0].path.rpartition(".")[0]):
            *scope, name = signal.path.split(".")
            shared = 0
            while shared < min(len(scope), len(opened)) and scope[shared] == opened[shared]:
                shared += 1
            header.extend(["$upscope $end"] * (len(opened) - shared))
            header.extend(f"$scope module {name} $end" for name in scope[shared:])
            kind = "wire" if signal.net else "reg"
            bits = f" [{signal.packed_range[0]}:{signal.packed_range[1]}]" if signal.packed_range else ""
            header.append(f"$var {kind} {signal.width} {code} {name}{bits} $end")
            opened = scope
        header.extend(["$upscope $end"] * len(opened))
        header.append("$enddefinitions $end")
        self.append(f"#{now // self.tick}")
        return {"name": self.filename, "text": "\n".join([*header, *self.lines, ""])}


class Engine:
    def __init__(self, tick, time_limit=None):
        self.active, self.inactive, self.nba = deque(), deque(), deque()
        self.future = []
        self.sequence = count()
        self.wave = Wave(tick)
        self.now = 0
        self.time_limit = time_limit
        self.test_errors = 0
        self.log = []
        self.log_bytes = 0
        self.steps = 0
        self.slot_steps = 0
        self.done = False
        self.reason = "Event queue empty"

    def charge(self):
        self.steps += 1
        self.slot_steps += 1
        if self.slot_steps > 100000:
            raise SimulationError(f"Time slot did not settle at tick {self.now // self.wave.tick}. "
                                  "Check for a zero-delay loop or combinational feedback.")
        if self.steps > 2_000_000:
            raise SimulationError("The run exceeded the event budget. Shorten the testbench or set a time limit.")

    def schedule(self, callback, delay, nba=False):
        if delay < 0:
            raise SimulationError("Negative delay is outside the supported simulation subset.")
        if delay:
            if (self.now + delay) // self.wave.tick > 2**53 - 1:
                raise SimulationError("A delay exceeds the representable simulation time.")
            heappush(self.future, (self.now + delay, next(self.sequence), nba, callback))
        else:
            (self.nba if nba else self.inactive).append(callback)

    def add_process(self, instructions):
        self.active.append(Process(self, instructions).advance)

    def drive(self, compute, targets, dependencies):
        driver = object()
        queued = False
        previous = ()

        def update():
            nonlocal queued, previous
            queued = False
            current = targets()
            value = compute()
            if current != previous and previous:
                commit(self, previous, Logic.fill(sum(target.width for target in previous), "z"), driver)
            commit(self, current, value.fit(sum(target.width for target in current)), driver)
            previous = current

        def request():
            nonlocal queued
            if not queued:
                queued = True
                self.active.append(update)

        for signal in dependencies:
            signal.listeners[request] = None
        request()

    def print(self, message):
        self.log_bytes += len(message) + 1
        if self.log_bytes <= 100000:
            self.log.append(message)

    def finish(self, reason):
        self.done = True
        self.reason = reason

    def run(self):
        while not self.done:
            if not self.active:
                if self.inactive:
                    self.active, self.inactive = self.inactive, self.active
                elif self.nba:
                    # Commit this NBA batch before running processes triggered by its writes.
                    while self.nba:
                        self.charge()
                        self.nba.popleft()()
                    continue
                else:
                    self.wave.flush(self.now)
                    if not self.future:
                        break
                    if self.time_limit is not None and self.future[0][0] > self.time_limit:
                        self.now = self.time_limit
                        self.finish("Time limit reached")
                        break
                    self.now = self.future[0][0]
                    self.slot_steps = 0
                    while self.future and self.future[0][0] == self.now:
                        _, _, nba, callback = heappop(self.future)
                        (self.nba if nba else self.active).append(callback)
                    continue
            self.charge()
            self.active.popleft()()
        self.wave.flush(self.now)
        return {"log": "\n".join(self.log), "wave": self.wave.export(self.now),
                "ticks": self.now // self.wave.tick, "timescale": timescale(self.wave.tick),
                "events": self.steps, "reason": self.reason}
