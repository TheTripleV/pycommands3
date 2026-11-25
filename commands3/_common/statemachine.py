"""Port of StateMachine.java (declarative state machines are commands)."""

from __future__ import annotations

from typing import Callable

from .command import DEFAULT_PRIORITY, Command
from .park import LIVE

StateSupplier = Callable[[], "State | None"]


class _Transition:
    __slots__ = ("next_supplier", "condition", "previous_signal")

    def __init__(self, next_supplier: StateSupplier, condition: Callable[[], bool]):
        self.next_supplier = next_supplier
        self.condition = condition
        self.previous_signal = False

    def should_transition(self) -> bool:
        current = bool(self.condition())
        rising = current and not self.previous_signal
        self.previous_signal = current
        return rising

    def next_state(self) -> "State | None":
        return self.next_supplier()


class _Completion:
    __slots__ = ("next_supplier", "condition")

    def __init__(self, next_supplier: StateSupplier, condition: Callable[[], bool]):
        self.next_supplier = next_supplier
        self.condition = condition


class State:
    """A state in a state machine: one command, plus transitions out of it."""

    def __init__(self, machine: "StateMachine", command: Command):
        self._machine = machine
        self._command = command
        self._completions: list[_Completion] = []
        self._default_next: StateSupplier = lambda: None
        self._transitions: list[_Transition] = []
        self._enter: list[Callable[[], None]] = []
        self._exit: list[Callable[[], None]] = []

    def command(self) -> Command:
        return self._command

    def _next_state(self) -> "State | None":
        for c in self._completions:
            if c.condition():
                return c.next_supplier()
        return self._default_next()

    def _add_completion(self, condition: Callable[[], bool], nxt: StateSupplier) -> None:
        self._completions = [c for c in self._completions if c.condition is not condition]
        self._completions.append(_Completion(nxt, condition))

    def on_enter(self, callback: Callable[[], None]) -> None:
        if callback is None:
            raise TypeError("callback cannot be None")
        self._enter.append(callback)

    def on_exit(self, callback: Callable[[], None]) -> None:
        if callback is None:
            raise TypeError("callback cannot be None")
        self._exit.append(callback)

    def switch_to(self, to: "State | StateSupplier") -> "TransitionNeedsConditionStage":
        if to is None:
            raise TypeError("to cannot be None")
        if isinstance(to, State) and to._machine is not self._machine:
            raise ValueError("Cannot transition to a state in a different state machine")
        return TransitionNeedsTargetStage([self]).to(to)

    def exit_state_machine(self) -> "TransitionNeedsConditionStage":
        return TransitionNeedsConditionStage([self], lambda: None)

    def __repr__(self) -> str:
        return f"State[{self._command.name()}]"


class TransitionNeedsTargetStage:
    def __init__(self, from_states: list[State]):
        self._from = from_states

    def to(self, to: "State | StateSupplier") -> "TransitionNeedsConditionStage":
        if to is None:
            raise TypeError("to cannot be None")
        if isinstance(to, State):
            for s in self._from:
                if s._machine is not to._machine:
                    raise ValueError("Cannot transition to a state in a different state machine")
            return TransitionNeedsConditionStage(self._from, lambda: to)
        return TransitionNeedsConditionStage(self._from, to)

    def to_exit_state_machine(self) -> "TransitionNeedsConditionStage":
        return TransitionNeedsConditionStage(self._from, lambda: None)


class TransitionNeedsConditionStage:
    def __init__(self, from_states: list[State], to: StateSupplier):
        self._from = from_states
        self._to = to

    def when(self, condition: Callable[[], bool]) -> None:
        if condition is None:
            raise TypeError("condition cannot be None")
        t = _Transition(self._to, condition)
        for s in self._from:
            s._transitions.append(t)

    def when_complete(self) -> None:
        for s in self._from:
            s._default_next = self._to

    def when_complete_and(self, condition: Callable[[], bool]) -> None:
        if condition is None:
            raise TypeError("condition cannot be None")
        for s in self._from:
            s._add_completion(condition, self._to)


class StateMachine(Command):
    """A declarative state machine. It requires nothing itself; each state's command is
    forked as a child, so requirements are held only while that state is active."""

    _native = True

    def __init__(self, name: str):
        if name is None:
            raise TypeError("name cannot be None")
        self._name = name
        self._priority = DEFAULT_PRIORITY
        self._initial: State | None = None
        self._states: list[State] = []
        self._closed = False
        self._current: State | None = None

    def set_priority(self, priority: int) -> None:
        self._priority = priority

    def priority(self) -> int:
        return self._priority

    def name(self) -> str:
        return self._name

    def requirements(self) -> frozenset:
        return frozenset()

    def add_state(self, command: Command) -> State:
        if command is None:
            raise TypeError("command cannot be None")
        if self._closed:
            raise RuntimeError(
                "Cannot add new states to a state machine after switch_from_any() has been "
                "called with no arguments"
            )
        s = State(self, command)
        self._states.append(s)
        return s

    def switch_from_any(self, *states: State) -> TransitionNeedsTargetStage:
        if not states:
            self._closed = True
            return TransitionNeedsTargetStage(list(self._states))
        return TransitionNeedsTargetStage(list(states))

    def set_initial_state(self, initial_state: State) -> None:
        if initial_state is None:
            raise TypeError("initialState cannot be None")
        if initial_state._machine is not self:
            raise ValueError("Cannot set initial state in a different state machine")
        self._initial = initial_state

    def current_state(self) -> State | None:
        """The active state while running (telemetry / parent-machine conditions)."""
        return self._current

    def _verify(self, nxt: State | None) -> State | None:
        if nxt is None or nxt._machine is self:
            return nxt
        raise RuntimeError(
            "The next state does not belong to this state machine. Check the state for "
            + nxt._command.name()
        )

    def run(self, coroutine):
        co = coroutine
        if self._initial is None:
            raise RuntimeError(
                f"{self._name} does not have an initial state. Use .set_initial_state() to "
                "provide one."
            )
        sched = co._scheduler
        state: State | None = self._initial
        try:
            while state is not None:
                current: State = state
                self._current = current
                cmd = current._command
                co.fork(cmd)
                for cb in current._enter:
                    cb()
                did_yield = False
                transitioned = False
                while sched.is_running(cmd):
                    for t in current._transitions:
                        if t.should_transition():
                            for cb in current._exit:
                                cb()
                            sched.cancel(cmd)
                            state = self._verify(t.next_state())
                            transitioned = True
                            break
                    if transitioned:
                        break
                    yield LIVE
                    did_yield = True
                if transitioned:
                    continue
                for cb in current._exit:
                    cb()
                state = self._verify(current._next_state())
                if not did_yield and state is not None:
                    yield LIVE
        finally:
            self._current = None
