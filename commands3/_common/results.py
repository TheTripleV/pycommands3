"""Port of Scheduler.ScheduleResult, SchedulerEvent and ConflictDetector."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Collection, Iterable

if TYPE_CHECKING:
    from .command import Command
    from .mechanism import Mechanism


# ---------------------------------------------------------------- ScheduleResult -------


class ScheduleResult:
    command: "Command"

    def successful(self) -> bool:
        raise NotImplementedError

    def __bool__(self) -> bool:
        return self.successful()


class Successful(ScheduleResult):
    run_id: int

    def successful(self) -> bool:
        return True


class Failure(ScheduleResult):
    def successful(self) -> bool:
        return False


@dataclass(frozen=True)
class Success(Successful):
    command: "Command"
    run_id: int


@dataclass(frozen=True)
class AlreadyRunning(Successful):
    command: "Command"
    run_id: int


@dataclass(frozen=True)
class LowerPriorityThanRunningCommand(Failure):
    command: "Command"
    already_running: "Command"


@dataclass(frozen=True)
class LowerPriorityThanQueuedCommand(Failure):
    command: "Command"
    queued_command: "Command"


@dataclass(frozen=True)
class RequiresUnsafeMechanisms(Failure):
    command: "Command"
    unsafe_mechanisms: frozenset = field(default_factory=frozenset)


# ---------------------------------------------------------------- SchedulerEvent -------


@dataclass(frozen=True)
class SchedulerEvent:
    command: "Command"
    timestamp_nanos: int


@dataclass(frozen=True)
class Scheduled(SchedulerEvent):
    pass


@dataclass(frozen=True)
class Mounted(SchedulerEvent):
    pass


@dataclass(frozen=True)
class Yielded(SchedulerEvent):
    pass


@dataclass(frozen=True)
class Completed(SchedulerEvent):
    pass


@dataclass(frozen=True)
class CompletedWithError(SchedulerEvent):
    error: BaseException = field(default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class Canceled(SchedulerEvent):
    pass


@dataclass(frozen=True)
class Interrupted(SchedulerEvent):
    interrupter: "Command" = field(default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class ForkFailure(SchedulerEvent):
    failures: tuple = ()


# --------------------------------------------------------------- ConflictDetector ------


@dataclass(frozen=True)
class Conflict:
    a: "Command"
    b: "Command"
    shared_requirements: frozenset

    def description(self) -> str:
        shared = ", ".join(sorted(m.get_name() for m in self.shared_requirements))
        return f"{self.a.name()} and {self.b.name()} both require {shared}"


class ConflictDetector:
    @staticmethod
    def find_all_conflicts(commands: Iterable["Command"]) -> list[Conflict]:
        cmds = list(commands)
        out: list[Conflict] = []
        for i, a in enumerate(cmds):
            for b in cmds[i + 1 :]:
                if a is b:
                    continue
                if a.conflicts_with(b):
                    out.append(
                        Conflict(a, b, frozenset(a.requirements()) & frozenset(b.requirements()))
                    )
        return out

    @staticmethod
    def throw_if_conflicts(commands: Collection["Command"]) -> None:
        if len(commands) <= 1:
            return
        conflicts = ConflictDetector.find_all_conflicts(commands)
        if not conflicts:
            return
        raise ValueError(
            "Commands running in parallel cannot share requirements: "
            + "; ".join(c.description() for c in conflicts)
        )
