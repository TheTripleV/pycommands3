"""Port of org.wpilib.command3.sysid.SysIdRoutine (volts and seconds as floats)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Callable

from wpilib.sysid import State, SysIdRoutineLog

from .._common.command import Command, StagedCommandBuilder
from .._common.mechanism import Mechanism
from .._common.park import LIVE
from .._common.time import get_timestamp

__all__ = ["SysIdRoutine", "Config", "SysIdMechanism", "Direction", "State", "SysIdRoutineLog"]


class Direction(enum.Enum):
    FORWARD = 1.0
    REVERSE = -1.0


@dataclass
class Config:
    ramp_rate: float = 1.0       # volts per second
    step_voltage: float = 7.0    # volts
    timeout: float = 10.0        # seconds
    record_state: Callable[[State], None] | None = None

    DEFAULT_RAMP_RATE = 1.0
    DEFAULT_STEP_VOLTAGE = 7.0
    DEFAULT_TIMEOUT = 10.0


@dataclass
class SysIdMechanism:
    drive: Callable[[float], None]
    log: Callable[[SysIdRoutineLog], None] | None
    mechanism: Mechanism
    name: str | None = None

    def log_name(self) -> str:
        return self.name if self.name is not None else self.mechanism.get_name()

    def do_log(self, log: SysIdRoutineLog) -> None:
        if self.log is not None:
            self.log(log)


_STATE_NAMES = {
    State.QUASISTATIC_FORWARD: "quasistatic-forward",
    State.QUASISTATIC_REVERSE: "quasistatic-reverse",
    State.DYNAMIC_FORWARD: "dynamic-forward",
    State.DYNAMIC_REVERSE: "dynamic-reverse",
}


class SysIdRoutine(SysIdRoutineLog):
    def __init__(self, config: Config, mechanism: SysIdMechanism):
        super().__init__(mechanism.log_name())
        self._config = config
        self._mechanism = mechanism
        self._record_state = config.record_state or self.record_state

    def _routine(self, state: State, output: Callable[[float], float]) -> Command:
        cfg, mech = self._config, self._mechanism

        def op(co):
            start = get_timestamp()
            while get_timestamp() - start < cfg.timeout:
                mech.drive(output(get_timestamp() - start))
                mech.do_log(self)
                self._record_state(state)
                yield LIVE

        return (StagedCommandBuilder().requiring(mech.mechanism)._executing_native(op)
                .when_exited(self._stop_routine)
                .named(f"sysid-{_STATE_NAMES[state]}-{mech.log_name()}"))

    def quasistatic(self, direction: Direction) -> Command:
        sign = direction.value
        state = State.QUASISTATIC_FORWARD if sign > 0 else State.QUASISTATIC_REVERSE
        return self._routine(state, lambda t: self._config.ramp_rate * t * sign)

    def dynamic(self, direction: Direction) -> Command:
        sign = direction.value
        state = State.DYNAMIC_FORWARD if sign > 0 else State.DYNAMIC_REVERSE
        return self._routine(state, lambda t: self._config.step_voltage * sign)

    def _stop_routine(self) -> None:
        self._mechanism.drive(0.0)
        self._record_state(State.NONE)
