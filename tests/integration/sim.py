"""Minimal robot simulation harness: real robot class, real HAL sim, stepped timing."""

from __future__ import annotations

import threading
import time

import hal
import wpilib
from wpilib.simulation import DriverStationSim as DS
from wpilib.simulation import pause_timing, step_timing

MODE_BY_BITS = {1: hal.RobotMode.AUTONOMOUS, 2: hal.RobotMode.TELEOPERATED,
                3: hal.RobotMode.UTILITY}


class SimRobot:
    def __init__(self, robot_cls):
        pause_timing()
        DS.reset_data()
        self.robot = None
        self.error: BaseException | None = None
        self._ready = threading.Event()

        def main():
            try:
                self.robot = robot_cls()
                self._ready.set()
                self.robot.start_competition()
            except BaseException as e:  # surfaced by the test thread
                self.error = e
                self._ready.set()

        self._thread = threading.Thread(target=main, daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self.error:
            raise self.error
        for _ in range(100):  # let start_competition reach its loop
            if hal.get_program_started() if hasattr(hal, "get_program_started") else True:
                break
            time.sleep(0.01)
        time.sleep(0.05)
        self.ds_attached(True)

    # ------------------------------------------------------------------ inputs -----
    def ds_attached(self, attached: bool) -> None:
        DS.set_ds_attached(attached)
        DS.notify_new_data()

    def select_opmode(self, name: str) -> None:
        for opt in DS.get_opmode_options():
            if opt.name == name:
                DS.set_opmode(opt.id)
                DS.set_robot_mode(MODE_BY_BITS[(opt.id >> 56) & 3])
                DS.notify_new_data()
                return
        raise KeyError(f"no opmode {name!r}; have {[o.name for o in DS.get_opmode_options()]}")

    def mode(self, mode: hal.RobotMode) -> None:
        DS.set_robot_mode(mode)
        DS.notify_new_data()

    def enable(self, enabled: bool = True) -> None:
        DS.set_enabled(enabled)
        DS.notify_new_data()

    def gamepad(self, port: int, *, buttons: dict[int, bool] | None = None,
                axes: dict[int, float] | None = None) -> None:
        DS.set_joystick_buttons_maximum_index(port, 26)
        DS.set_joystick_axes_maximum_index(port, 6)
        DS.set_joystick_is_gamepad(port, True)
        for b, v in (buttons or {}).items():
            DS.set_joystick_button(port, b, v)
        for a, v in (axes or {}).items():
            DS.set_joystick_axis(port, a, v)
        DS.notify_new_data()

    # ------------------------------------------------------------------- time -------
    def step(self, cycles: int = 1, dt: float = 0.02, each=None) -> None:
        for _ in range(cycles):
            step_timing(dt)
            if self.error:
                raise self.error
            if each is not None:
                each()

    def stop(self) -> None:
        if self.robot is not None:
            self.robot.end_competition()
            step_timing(0.02)
        self._thread.join(5)
