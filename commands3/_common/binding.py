"""Port of BindingScope, Binding and BindingType."""

from __future__ import annotations

import enum
import sys
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import robot_state
from .robot_state import RobotMode

if TYPE_CHECKING:
    from .command import Command
    from .scheduler import SchedulerBase


class BindingType(enum.Enum):
    IMMEDIATE = enum.auto()
    SCHEDULE_ON_RISING_EDGE = enum.auto()
    SCHEDULE_ON_FALLING_EDGE = enum.auto()
    SCHEDULE_WHILE_HIGH = enum.auto()
    TOGGLE_ON_RISING_EDGE = enum.auto()
    TOGGLE_ON_FALLING_EDGE = enum.auto()
    RUN_WHILE_HIGH = enum.auto()
    RUN_WHILE_LOW = enum.auto()
    CONTINUOUSLY_SCHEDULE_WHILE_HIGH = enum.auto()
    CONTINUOUSLY_SCHEDULE_WHILE_LOW = enum.auto()


class BindingScope:
    def active(self) -> bool:
        raise NotImplementedError

    @staticmethod
    def create_narrowest_scope(scheduler: "SchedulerBase") -> "BindingScope":
        current = scheduler.current_command()
        fetcher = robot_state.get_fetcher()
        if current is not None:
            return ForCommand(scheduler, current)
        opmode = fetcher.get_opmode_id()
        if opmode != 0:
            return ForOpmode(opmode)
        mode = fetcher.get_robot_mode()
        if mode == RobotMode.AUTONOMOUS:
            return AutonomousMode.INSTANCE
        if mode == RobotMode.TELEOPERATED:
            return TeleopMode.INSTANCE
        if mode == RobotMode.UTILITY:
            return UtilityMode.INSTANCE
        return Global.INSTANCE


class Global(BindingScope):
    INSTANCE: "Global"

    def active(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "Global"


Global.INSTANCE = Global()


@dataclass(frozen=True, eq=True)
class ForCommand(BindingScope):
    scheduler: "SchedulerBase" = field(compare=False)
    command: "Command"

    def active(self) -> bool:
        return self.scheduler.is_running(self.command)


@dataclass(frozen=True)
class ForOpmode(BindingScope):
    opmode_id: int

    def active(self) -> bool:
        return robot_state.get_fetcher().get_opmode_id() == self.opmode_id


class RobotModeScope(BindingScope):
    MODE: RobotMode

    def active(self) -> bool:
        return robot_state.get_fetcher().get_robot_mode() == self.MODE

    def __repr__(self) -> str:
        return type(self).__name__


class AutonomousMode(RobotModeScope):
    MODE = RobotMode.AUTONOMOUS
    INSTANCE: "AutonomousMode"


class TeleopMode(RobotModeScope):
    MODE = RobotMode.TELEOPERATED
    INSTANCE: "TeleopMode"


class UtilityMode(RobotModeScope):
    MODE = RobotMode.UTILITY
    INSTANCE: "UtilityMode"


AutonomousMode.INSTANCE = AutonomousMode()
TeleopMode.INSTANCE = TeleopMode()
UtilityMode.INSTANCE = UtilityMode()


def capture_site(skip: int = 2, limit: int = 40) -> traceback.StackSummary:
    """Cheap capture of the scheduling site (Java stores a Throwable per binding)."""
    frame = sys._getframe(skip)
    return traceback.StackSummary.extract(
        traceback.walk_stack(frame), limit=limit, lookup_lines=False
    )


@dataclass(eq=False)
class Binding:
    scope: BindingScope
    type: BindingType
    command: "Command"
    site: traceback.StackSummary | None = None
