"""Port of CommandRunTracker: watches a set of (command, run id) pairs for completion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .results import Successful
    from .scheduler import SchedulerBase


class CommandRunTracker:
    __slots__ = ("_scheduler", "_commands", "_ids", "_active", "_total")

    def __init__(self, scheduler: "SchedulerBase", commands: list, run_ids: list[int]):
        self._scheduler = scheduler
        self._commands = commands
        self._ids = run_ids
        self._total = len(commands)
        # indices still believed to be running (run id 0 == one-shot that already finished)
        self._active = [i for i, rid in enumerate(run_ids) if rid != 0]

    @staticmethod
    def of(scheduler: "SchedulerBase", forks: Iterable["Successful"]) -> "CommandRunTracker":
        if scheduler is None:
            raise TypeError("scheduler cannot be None")
        cmds, ids = [], []
        for i, f in enumerate(forks):
            if f is None:
                raise TypeError(f"forks[{i}] cannot be None")
            cmds.append(f.command)
            ids.append(f.run_id)
        return CommandRunTracker(scheduler, cmds, ids)

    def remaining_count(self) -> int:
        return len(self._active)

    def are_all_running(self) -> bool:
        if self._total == 0 or len(self._active) < self._total:
            return False
        for i in self._active:
            if self._scheduler.run_id(self._commands[i]) != self._ids[i]:
                self._active.remove(i)
                return False
        return True

    def is_any_running(self) -> bool:
        if not self._active:
            return False
        still = []
        found = False
        for i in self._active:
            if self._scheduler.run_id(self._commands[i]) == self._ids[i]:
                still.append(i)
                found = True
                # Java stops at the first running entry; keep the rest unexamined.
                still.extend(j for j in self._active if j > i)
                break
        self._active = still
        return found
