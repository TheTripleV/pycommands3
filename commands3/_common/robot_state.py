"""Port of RobotStateFetcher: where the scheduler learns enabled state, robot mode and opmode."""

from __future__ import annotations

import enum


class RobotMode(enum.IntEnum):
    """Mirrors ``hal.RobotMode`` so the core does not need HAL to be importable."""

    UNKNOWN = 0
    AUTONOMOUS = 1
    TELEOPERATED = 2
    UTILITY = 3


class RobotStateFetcher:
    def get_opmode_id(self) -> int:
        raise NotImplementedError

    def get_opmode_name(self) -> str:
        raise NotImplementedError

    def get_robot_mode(self) -> RobotMode:
        raise NotImplementedError

    def is_enabled(self) -> bool:
        raise NotImplementedError


class DriverStationRobotStateFetcher(RobotStateFetcher):
    def __init__(self) -> None:
        from wpilib import RobotState

        self._rs = RobotState

    def get_opmode_id(self) -> int:
        return self._rs.get_opmode_id()

    def get_opmode_name(self) -> str:
        return self._rs.get_opmode()

    def get_robot_mode(self) -> RobotMode:
        return RobotMode(int(self._rs.get_robot_mode().value))

    def is_enabled(self) -> bool:
        return self._rs.is_enabled()


class MockRobotStateFetcher(RobotStateFetcher):
    """Mutable fetcher for tests (CommandTestBase / MockHardwareExtension)."""

    def __init__(self) -> None:
        self.opmode_id = 0
        self.opmode_name = ""
        self.robot_mode = RobotMode.UNKNOWN
        self.enabled = True

    def get_opmode_id(self) -> int:
        return self.opmode_id

    def get_opmode_name(self) -> str:
        return self.opmode_name

    def get_robot_mode(self) -> RobotMode:
        return self.robot_mode

    def is_enabled(self) -> bool:
        return self.enabled


_fetcher: RobotStateFetcher | None = None


def get_fetcher() -> RobotStateFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = DriverStationRobotStateFetcher()
    return _fetcher


def set_fetcher(fetcher: RobotStateFetcher | None) -> None:
    """Install a fetcher; ``None`` restores the DriverStation-backed default."""
    global _fetcher
    _fetcher = fetcher
