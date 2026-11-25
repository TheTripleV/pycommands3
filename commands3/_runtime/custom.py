"""Custom runtime: the scheduler steps each command's task directly. No event loop."""

from __future__ import annotations

from .._common.scheduler import CommandState, Outcome, SchedulerBase, step_task


class CustomScheduler(SchedulerBase):
    runtime_name = "custom"

    def _mount_step(self, state) -> Outcome:
        if state.task is None:
            co = state.coroutine
            cmd = state.command
            assert cmd is not None  # sideload tasks are created eagerly
            state.task = self._make_task(state, lambda: cmd.run(co), cmd._native)
        return step_task(state.task)
