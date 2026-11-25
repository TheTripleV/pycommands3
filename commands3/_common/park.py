"""Park states: port of CoroutineState.

Every suspension point in every front-end ends up handing one of these to the scheduler.
The scheduler polls ``should_mount()`` before remounting a command, so parked commands cost
one predicate call per cycle instead of a full resume/suspend.
"""

from __future__ import annotations

from typing import Callable, Generator, Any

from .time import get_time_ns


class Park:
    __slots__ = ()

    def should_mount(self) -> bool:
        return True


class Live(Park):
    __slots__ = ()

    def __repr__(self) -> str:
        return "LIVE"


class Frozen(Park):
    __slots__ = ()

    def should_mount(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "FROZEN"


LIVE = Live()
FROZEN = Frozen()


class ParkedUntil(Park):
    __slots__ = ("target_timestamp_nanos",)

    def __init__(self, target_timestamp_nanos: int):
        self.target_timestamp_nanos = target_timestamp_nanos

    def should_mount(self) -> bool:
        return get_time_ns() >= self.target_timestamp_nanos

    def __repr__(self) -> str:
        return f"ParkedUntil[{self.target_timestamp_nanos}ns]"


class ParkedOnCondition(Park):
    __slots__ = ("condition",)

    def __init__(self, condition: Callable[[], bool]):
        self.condition = condition

    def condition_met(self) -> bool:
        return bool(self.condition())

    def should_mount(self) -> bool:
        return bool(self.condition())

    def __repr__(self) -> str:
        return "ParkedOnCondition[dynamic]"


class ParkedWhile(Park):
    __slots__ = ("condition",)

    def __init__(self, condition: Callable[[], bool]):
        self.condition = condition

    def condition_met(self) -> bool:
        return not self.condition()

    def should_mount(self) -> bool:
        return not self.condition()

    def __repr__(self) -> str:
        return "ParkedWhile[dynamic]"


class ParkedOnFuture(Park):
    """asyncio runtime only: waiting on a foreign awaitable (e.g. ``asyncio.to_thread``)."""

    __slots__ = ("future",)

    def __init__(self, future: Any):
        self.future = future

    def should_mount(self) -> bool:
        return self.future.done()

    def __repr__(self) -> str:
        return f"ParkedOnFuture[{self.future!r}]"


# A primitive op: a plain generator yielding Park objects and returning a value. Every
# front-end exposes these through its own syntax; framework-internal commands use them directly.
Op = Generator[Park, Any, Any]
