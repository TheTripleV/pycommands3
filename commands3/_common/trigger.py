"""Port of Trigger.java (+ a Debouncer driven by the framework clock)."""

from __future__ import annotations

import enum
import weakref
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from .binding import Binding, BindingScope, BindingType, Global, RobotModeScope, capture_site
from .registry import get_default_scheduler
from .time import get_timestamp

if TYPE_CHECKING:
    from .command import Command
    from .scheduler import EventLoop, SchedulerBase


class DebounceType(enum.Enum):
    RISING = 0
    FALLING = 1
    BOTH = 2


class Debouncer:
    """Pure-Python port of wpimath Debouncer using the commands3 time source."""

    def __init__(self, debounce_time: float, type: DebounceType = DebounceType.RISING):
        self._time = debounce_time
        self._type = type
        self._baseline = type == DebounceType.FALLING
        self._prev = get_timestamp()

    def _reset(self) -> None:
        self._prev = get_timestamp()

    def _elapsed(self) -> bool:
        return get_timestamp() - self._prev >= self._time

    def calculate(self, value: bool) -> bool:
        if value == self._baseline:
            self._reset()
        if self._elapsed():
            if self._type == DebounceType.BOTH:
                self._baseline = value
                self._reset()
            return value
        return self._baseline


class _Signal(enum.Enum):
    HIGH = 1
    LOW = 0


class Trigger:
    """A boolean condition that schedules and cancels commands on its edges and levels.

    ``Trigger(condition)``, ``Trigger(scheduler, condition)`` or
    ``Trigger(scheduler, loop, condition)``, mirroring the Java constructors.
    """

    Signal = _Signal

    def __init__(self, *args):
        if len(args) == 1:
            scheduler, loop, condition = get_default_scheduler(), None, args[0]
        elif len(args) == 2:
            scheduler, loop, condition = args[0], None, args[1]
        elif len(args) == 3:
            scheduler, loop, condition = args
        else:
            raise TypeError("Trigger(condition) / Trigger(scheduler, condition) / "
                            "Trigger(scheduler, loop, condition)")
        if scheduler is None:
            raise TypeError("scheduler cannot be None")
        if condition is None:
            raise TypeError("condition cannot be None")
        if loop is None:
            loop = scheduler.get_default_event_loop()
        self._scheduler: SchedulerBase = scheduler
        self._loop = loop
        self._condition = condition
        scope = BindingScope.create_narrowest_scope(scheduler)
        self._lifetime_scope = Global.INSTANCE if isinstance(scope, RobotModeScope) else scope
        self._previous_signal: _Signal | None = None
        self._cached_signal: _Signal | None = None
        self._bindings: dict[BindingType, list[Binding]] = {}
        scheduler._add_bound_trigger(self)
        loop.bind_weak(self, Trigger._poll)

    # ---------------------------------------------------------------- bindings ---------
    def on_true(self, command: "Command") -> "Trigger":
        return self._add(BindingType.SCHEDULE_ON_RISING_EDGE, command, "on_true")

    def if_true(self, command: "Command") -> "Trigger":
        return self._add(BindingType.SCHEDULE_WHILE_HIGH, command, "if_true")

    def on_false(self, command: "Command") -> "Trigger":
        return self._add(BindingType.SCHEDULE_ON_FALLING_EDGE, command, "on_false")

    def while_true(self, command: "Command") -> "Trigger":
        return self._add(BindingType.RUN_WHILE_HIGH, command, "while_true")

    def while_false(self, command: "Command") -> "Trigger":
        return self._add(BindingType.RUN_WHILE_LOW, command, "while_false")

    def retry_while_true(self, command: "Command") -> "Trigger":
        return self._add(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_HIGH, command, "retry_while_true")

    def retry_while_false(self, command: "Command") -> "Trigger":
        return self._add(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_LOW, command, "retry_while_false")

    def toggle_on_true(self, command: "Command") -> "Trigger":
        return self._add(BindingType.TOGGLE_ON_RISING_EDGE, command, "toggle_on_true")

    def toggle_on_false(self, command: "Command") -> "Trigger":
        return self._add(BindingType.TOGGLE_ON_FALLING_EDGE, command, "toggle_on_false")

    def _add(self, binding_type: BindingType, command: "Command", what: str) -> "Trigger":
        if command is None:
            raise TypeError(f"command cannot be None ({what})")
        scope = BindingScope.create_narrowest_scope(self._scheduler)
        self._add_binding(scope, binding_type, command)
        return self

    def _add_binding(self, scope: BindingScope, binding_type: BindingType,
                     command: "Command") -> None:
        self._bindings.setdefault(binding_type, []).append(
            Binding(scope, binding_type, command, capture_site(3))
        )

    # -------------------------------------------------------------- composition --------
    def __call__(self) -> bool:
        return self._cached_signal is _Signal.HIGH

    get_as_boolean = __call__

    def and_(self, other: Callable[[], bool]) -> "Trigger":
        return Trigger(self._scheduler, self._loop, lambda: self() and bool(other()))

    def or_(self, other: Callable[[], bool]) -> "Trigger":
        return Trigger(self._scheduler, self._loop, lambda: self() or bool(other()))

    def negate(self) -> "Trigger":
        return Trigger(self._scheduler, self._loop, lambda: not self())

    __and__ = and_
    __or__ = or_
    __invert__ = negate

    def debounce(self, seconds: float, type: DebounceType = DebounceType.RISING) -> "Trigger":
        d = Debouncer(seconds, type)
        return Trigger(self._scheduler, self._loop, lambda: d.calculate(self()))

    def rising_edge(self) -> "Trigger":
        return Trigger(
            self._scheduler, self._loop,
            lambda: self._cached_signal is _Signal.HIGH and self._previous_signal is _Signal.LOW,
        )

    def falling_edge(self) -> "Trigger":
        return Trigger(
            self._scheduler, self._loop,
            lambda: self._cached_signal is _Signal.LOW and self._previous_signal is _Signal.HIGH,
        )

    def multi_press(self, press_count: int, seconds: float) -> "Trigger":
        if seconds is None:
            raise TypeError("duration cannot be None")
        if seconds <= 0:
            return Trigger(self._scheduler, self._loop, lambda: False)
        if press_count <= 0:
            return Trigger(self._scheduler, self._loop, lambda: True)
        timestamps: deque[float] = deque()
        state = {"rising": False}

        def cond() -> bool:
            if self._cached_signal is _Signal.HIGH and self._previous_signal is not _Signal.HIGH:
                if not state["rising"]:
                    timestamps.append(get_timestamp())
                    state["rising"] = True
            elif self._cached_signal is not _Signal.HIGH:
                state["rising"] = False
            now = get_timestamp()
            while timestamps and now - timestamps[0] > seconds + 1e-9:
                timestamps.popleft()
            return len(timestamps) >= press_count

        return Trigger(self._scheduler, self._loop, cond)

    # --------------------------------------------------------------------- polling -----
    def _poll(self) -> None:
        self._clear_stale_bindings()
        self._previous_signal = self._cached_signal
        self._cached_signal = _Signal.HIGH if self._condition() else _Signal.LOW
        if self._cached_signal is _Signal.HIGH:
            self._schedule_bindings(BindingType.SCHEDULE_WHILE_HIGH)
            self._schedule_bindings(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_HIGH)
        else:
            self._schedule_bindings(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_LOW)
        if self._cached_signal is self._previous_signal:
            return
        if self._cached_signal is _Signal.HIGH:
            self._schedule_bindings(BindingType.SCHEDULE_ON_RISING_EDGE)
            self._schedule_bindings(BindingType.RUN_WHILE_HIGH)
            self._cancel_bindings(BindingType.RUN_WHILE_LOW)
            self._cancel_bindings(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_LOW)
            self._toggle_bindings(BindingType.TOGGLE_ON_RISING_EDGE)
        else:
            self._schedule_bindings(BindingType.SCHEDULE_ON_FALLING_EDGE)
            self._schedule_bindings(BindingType.RUN_WHILE_LOW)
            self._cancel_bindings(BindingType.RUN_WHILE_HIGH)
            self._cancel_bindings(BindingType.CONTINUOUSLY_SCHEDULE_WHILE_HIGH)
            self._toggle_bindings(BindingType.TOGGLE_ON_FALLING_EDGE)

    def _clear_stale_bindings(self) -> None:
        for bindings in self._bindings.values():
            for b in list(bindings):
                if b.scope.active():
                    continue
                bindings.remove(b)
                self._scheduler.cancel(b.command)

    def _schedule_bindings(self, t: BindingType) -> None:
        for b in list(self._bindings.get(t, ())):
            self._scheduler._schedule_binding(b)

    def _cancel_bindings(self, t: BindingType) -> None:
        for b in list(self._bindings.get(t, ())):
            self._scheduler.cancel(b.command)

    def _toggle_bindings(self, t: BindingType) -> None:
        for b in list(self._bindings.get(t, ())):
            if self._scheduler.is_scheduled_or_running(b.command):
                self._scheduler.cancel(b.command)
            else:
                self._scheduler._schedule_binding(b)

    def get_cached_signal(self) -> "_Signal | None":
        return self._cached_signal

    def get_previous_signal(self) -> "_Signal | None":
        return self._previous_signal

    def _is_scope_active(self) -> bool:
        return self._lifetime_scope.active()


class EventLoop:
    """Minimal event loop holding weak trigger references (Java uses wpilib EventLoop.bindWeak)."""

    def __init__(self) -> None:
        self._entries: list[tuple[weakref.ref, Callable]] = []
        self._strong: list[Callable[[], None]] = []

    def bind(self, action: Callable[[], None]) -> None:
        self._strong.append(action)

    def bind_weak(self, owner: Any, method: Callable[[Any], None]) -> None:
        self._entries.append((weakref.ref(owner), method))

    def poll(self) -> None:
        for action in list(self._strong):
            action()
        for ref, method in list(self._entries):
            owner = ref()
            if owner is not None:
                method(owner)
        self._entries = [e for e in self._entries if e[0]() is not None]

    def clear(self) -> None:
        self._entries.clear()
        self._strong.clear()
