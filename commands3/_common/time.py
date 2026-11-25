"""Time source used by every part of the framework (port of RobotController.getTime usage).

Defaults to ``wpilib.RobotController.get_time`` (nanoseconds), which follows simulation
timing (``wpilib.simulation.step_timing``). Tests may install their own source.
"""

from __future__ import annotations

import time as _time
from typing import Callable

_source: Callable[[], int] | None = None


def _default_source() -> Callable[[], int]:
    try:
        from wpilib import RobotController

        return RobotController.get_time
    except Exception:  # pragma: no cover - wpilib missing or HAL not initialized
        return _time.monotonic_ns


def get_time_ns() -> int:
    """Current time in nanoseconds."""
    global _source
    if _source is None:
        _source = _default_source()
    return _source()


def get_timestamp() -> float:
    """Current time in seconds."""
    return get_time_ns() / 1e9


def set_time_source(source: Callable[[], int] | None) -> None:
    """Install a nanosecond time source; ``None`` restores the default."""
    global _source
    _source = source
