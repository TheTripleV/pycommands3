"""Port of Mechanism.java."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .command import LOWEST_PRIORITY, Command, NeedsNameBuilderStage, StagedCommandBuilder
from .park import LIVE
from .registry import get_default_scheduler

if TYPE_CHECKING:
    from .scheduler import SchedulerBase


class Mechanism:
    """A physical (or logical) resource that commands require exclusive ownership of.

    Subclass it, or instantiate it directly with a name and (optionally) a scheduler.
    """

    def __init__(self, name: str | None = None, scheduler: "SchedulerBase | None" = None,
                 controllable_during_disabled: bool | None = None):
        if name is not None:
            self._mech_name = name
        if scheduler is not None:
            self._mech_scheduler = scheduler
        if controllable_during_disabled is not None:
            self._mech_controllable_during_disabled = controllable_during_disabled

    def get_registered_scheduler(self) -> "SchedulerBase":
        s = getattr(self, "_mech_scheduler", None)
        return s if s is not None else get_default_scheduler()

    def controllable_during_disabled(self) -> bool:
        return getattr(self, "_mech_controllable_during_disabled", False)

    def get_name(self) -> str:
        return getattr(self, "_mech_name", None) or type(self).__name__

    def set_default_command(self, default_command: Command) -> None:
        self.get_registered_scheduler().set_default_command(self, default_command)

    def get_default_command(self) -> Command | None:
        return self.get_registered_scheduler().get_default_command_for(self)

    def run(self, command_body: Callable[[Any], Any]) -> NeedsNameBuilderStage:
        return StagedCommandBuilder().requiring(self).executing(command_body)

    def run_repeatedly(self, loop_body: Callable[[], None]) -> NeedsNameBuilderStage:
        def op(co):
            while True:
                loop_body()
                yield LIVE

        return StagedCommandBuilder().requiring(self)._executing_native(op)

    def idle(self) -> Command:
        return (
            StagedCommandBuilder()
            .requiring(self)
            ._executing_native(lambda co: co.op_park())
            .with_priority(LOWEST_PRIORITY)
            .named(self.get_name() + "[IDLE]")
        )

    def idle_for(self, seconds: float) -> Command:
        return self.idle().with_timeout(seconds)

    def get_running_commands(self) -> list[Command]:
        return self.get_registered_scheduler().get_running_commands_for(self)

    def __repr__(self) -> str:
        return self.get_name()
