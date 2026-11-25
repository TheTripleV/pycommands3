"""Ports of the Java test helpers (DummyMechanism, NullCommand, PriorityCommand)."""

from commands3._common.api import Command, Mechanism
from commands3._frontends.asyncawait import Coroutine


class DummyMechanism(Mechanism):
    def __init__(self, name, scheduler, controllable_during_disabled=False):
        super().__init__(name, scheduler, controllable_during_disabled)


class NullCommand(Command):
    async def run(self, coroutine: Coroutine):
        await coroutine.park()

    def name(self):
        return "Null Command"

    def requirements(self):
        return frozenset()


class PriorityCommand(Command):
    def __init__(self, priority, *mechanisms):
        self._priority = priority
        self._reqs = frozenset(mechanisms)

    async def run(self, coroutine: Coroutine):
        await coroutine.park()

    def priority(self):
        return self._priority

    def requirements(self):
        return self._reqs

    def name(self):
        return f"PriorityCommand[priority={self._priority}, subsystems={set(self._reqs)}]"


class Ref:
    """Stand-in for AtomicBoolean/AtomicInteger/AtomicReference."""

    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, v):
        self.value = v

    def increment_and_get(self):
        self.value += 1
        return self.value

    def get_and_increment(self):
        v = self.value
        self.value += 1
        return v


def assert_event(events, cls, pred=lambda e: True, message="expected event not found"):
    assert any(isinstance(e, cls) and pred(e) for e in events), message
