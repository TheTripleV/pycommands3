"""Ports of CommandGenericHID, CommandJoystick, CommandGamepad helpers, InternalButton,
JoystickButton, POVButton, NetworkButton and RobotModeTriggers."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import wpilib

from .._common.registry import get_default_scheduler
from .._common.trigger import EventLoop, Trigger

if TYPE_CHECKING:
    from .._common.scheduler import SchedulerBase

MAX_DEVICES = 6


class CommandGenericHID:
    _lock = threading.Lock()

    def __init__(self, *args):
        """CommandGenericHID(port) | (hid) | (scheduler, port) | (scheduler, hid)"""
        if len(args) == 1:
            scheduler, target = get_default_scheduler(), args[0]
        elif len(args) == 2:
            scheduler, target = args
        else:
            raise TypeError("CommandGenericHID([scheduler,] port_or_hid)")
        self._scheduler: SchedulerBase = scheduler
        self._hid = wpilib.DriverStation.get_generic_hid(target) if isinstance(target, int) else target
        self._button_cache: dict = {}
        self._axis_lt_cache: dict = {}
        self._axis_gt_cache: dict = {}
        self._axis_mag_cache: dict = {}
        self._pov_cache: dict = {}

    @classmethod
    def get_command_generic_hid(cls, *args) -> "CommandGenericHID":
        """Shared per-(scheduler, port) instance so Trigger caches are shared."""
        if len(args) == 1:
            scheduler, port = get_default_scheduler(), args[0]
        else:
            scheduler, port = args
        with cls._lock:
            # Stored on the scheduler itself so the cache lives exactly as long as it does
            # (a module-level weak map can't work: each HID references its scheduler).
            hids = scheduler.__dict__.setdefault("_command_generic_hids", [None] * MAX_DEVICES)
            if hids[port] is None:
                hids[port] = cls(scheduler, port)
            return hids[port]

    def get_hid(self) -> wpilib.GenericHID:
        return self._hid

    def get_scheduler(self) -> "SchedulerBase":
        return self._scheduler

    def _loop(self, loop: EventLoop | None) -> EventLoop:
        return loop if loop is not None else self._scheduler.get_default_event_loop()

    def button(self, button: int, loop: EventLoop | None = None) -> Trigger:
        loop = self._loop(loop)
        cache = self._button_cache.setdefault(loop, {})
        if button not in cache:
            cache[button] = Trigger(self._scheduler, loop, lambda: self._hid.get_raw_button(button))
        return cache[button]

    def pov(self, angle: wpilib.POVDirection, pov: int = 0, loop: EventLoop | None = None) -> Trigger:
        loop = self._loop(loop)
        cache = self._pov_cache.setdefault(loop, {})
        key = pov * 16 + int(angle.value)
        if key not in cache:
            cache[key] = Trigger(self._scheduler, loop, lambda: self._hid.get_pov(pov) == angle)
        return cache[key]

    def pov_up(self) -> Trigger:
        return self.pov(wpilib.POVDirection.UP)

    def pov_up_right(self) -> Trigger:
        return self.pov(wpilib.POVDirection.UP_RIGHT)

    def pov_right(self) -> Trigger:
        return self.pov(wpilib.POVDirection.RIGHT)

    def pov_down_right(self) -> Trigger:
        return self.pov(wpilib.POVDirection.DOWN_RIGHT)

    def pov_down(self) -> Trigger:
        return self.pov(wpilib.POVDirection.DOWN)

    def pov_down_left(self) -> Trigger:
        return self.pov(wpilib.POVDirection.DOWN_LEFT)

    def pov_left(self) -> Trigger:
        return self.pov(wpilib.POVDirection.LEFT)

    def pov_up_left(self) -> Trigger:
        return self.pov(wpilib.POVDirection.UP_LEFT)

    def pov_center(self) -> Trigger:
        return self.pov(wpilib.POVDirection.CENTER)

    def axis_less_than(self, axis: int, threshold: float, loop: EventLoop | None = None) -> Trigger:
        loop = self._loop(loop)
        cache = self._axis_lt_cache.setdefault(loop, {})
        if (axis, threshold) not in cache:
            cache[(axis, threshold)] = Trigger(self._scheduler, loop,
                                               lambda: self.get_raw_axis(axis) < threshold)
        return cache[(axis, threshold)]

    def axis_greater_than(self, axis: int, threshold: float, loop: EventLoop | None = None) -> Trigger:
        loop = self._loop(loop)
        cache = self._axis_gt_cache.setdefault(loop, {})
        if (axis, threshold) not in cache:
            cache[(axis, threshold)] = Trigger(self._scheduler, loop,
                                               lambda: self.get_raw_axis(axis) > threshold)
        return cache[(axis, threshold)]

    def axis_magnitude_greater_than(self, axis: int, threshold: float,
                                    loop: EventLoop | None = None) -> Trigger:
        loop = self._loop(loop)
        cache = self._axis_mag_cache.setdefault(loop, {})
        if (axis, threshold) not in cache:
            cache[(axis, threshold)] = Trigger(self._scheduler, loop,
                                               lambda: abs(self.get_raw_axis(axis)) > threshold)
        return cache[(axis, threshold)]

    def get_raw_axis(self, axis: int) -> float:
        return self._hid.get_raw_axis(axis)

    def set_rumble(self, type: wpilib.GenericHID.RumbleType, value: float) -> None:
        self._hid.set_rumble(type, value)

    def is_connected(self) -> bool:
        return self._hid.is_connected()


class CommandJoystick:
    def __init__(self, *args):
        if len(args) == 1:
            scheduler, port = get_default_scheduler(), args[0]
        else:
            scheduler, port = args
        self._hid = CommandGenericHID.get_command_generic_hid(scheduler, port)
        self._joystick = wpilib.Joystick(self._hid.get_hid())

    def get_hid(self) -> CommandGenericHID:
        return self._hid

    def get_joystick(self) -> wpilib.Joystick:
        return self._joystick

    def trigger(self, loop: EventLoop | None = None) -> Trigger:
        return self._hid.button(int(wpilib.Joystick.ButtonType.TRIGGER_BUTTON.value), loop)

    def top(self, loop: EventLoop | None = None) -> Trigger:
        return self._hid.button(int(wpilib.Joystick.ButtonType.TOP_BUTTON.value), loop)

    def __getattr__(self, name):
        # Channel setters/getters and axis readers delegate to wpilib.Joystick
        # (set_x_channel, get_x, get_magnitude, get_direction_degrees, ...).
        return getattr(self._joystick, name)


class InternalButton(Trigger):
    """A trigger whose state is set from code."""

    def __init__(self, inverted: bool = False, scheduler: "SchedulerBase | None" = None):
        self._pressed = False
        self._inverted = inverted
        super().__init__(scheduler or get_default_scheduler(),
                         lambda: self._pressed != self._inverted)

    def set_inverted(self, inverted: bool) -> None:
        self._inverted = inverted

    def set_pressed(self, pressed: bool) -> None:
        self._pressed = pressed


def _raw_hid(joystick):
    """The wpilib HID behind a wpilib HID or any Command* controller wrapper (whose
    ``get_hid()`` may itself return a wrapper)."""
    hid = joystick
    while isinstance(hid, CommandGenericHID) or (
            not hasattr(hid, "get_raw_button") and hasattr(hid, "get_hid")):
        hid = hid.get_hid()
    return hid


class JoystickButton(Trigger):
    def __init__(self, joystick, button_number: int, scheduler: "SchedulerBase | None" = None):
        if joystick is None:
            raise TypeError("joystick cannot be None")
        hid = _raw_hid(joystick)
        super().__init__(scheduler or get_default_scheduler(),
                         lambda: hid.get_raw_button(button_number))


class POVButton(Trigger):
    def __init__(self, joystick, angle: wpilib.POVDirection, pov_number: int = 0,
                 scheduler: "SchedulerBase | None" = None):
        if joystick is None:
            raise TypeError("joystick cannot be None")
        hid = _raw_hid(joystick)
        super().__init__(scheduler or get_default_scheduler(),
                         lambda: hid.get_pov(pov_number) == angle)


class NetworkButton(Trigger):
    """NetworkButton(topic) | (subscriber) | (table, field) | (table_name, field) |
    (instance, table_name, field)."""

    def __init__(self, *args, scheduler: "SchedulerBase | None" = None):
        import ntcore

        if len(args) == 1 and isinstance(args[0], ntcore.BooleanTopic):
            sub = args[0].subscribe(False)
        elif len(args) == 1:
            sub = args[0]
        elif len(args) == 2 and isinstance(args[0], ntcore.NetworkTable):
            sub = args[0].get_boolean_topic(args[1]).subscribe(False)
        elif len(args) == 2:
            inst = ntcore.NetworkTableInstance.get_default()
            sub = inst.get_table(args[0]).get_boolean_topic(args[1]).subscribe(False)
        elif len(args) == 3:
            sub = args[0].get_table(args[1]).get_boolean_topic(args[2]).subscribe(False)
        else:
            raise TypeError("NetworkButton(...) arguments not understood")
        self._sub = sub
        super().__init__(scheduler or get_default_scheduler(),
                         lambda: sub.get_topic().get_instance().is_connected() and sub.get())


class RobotModeTriggers:
    @staticmethod
    def autonomous(scheduler: "SchedulerBase | None" = None) -> Trigger:
        return Trigger(scheduler or get_default_scheduler(), wpilib.RobotState.is_autonomous_enabled)

    @staticmethod
    def teleop(scheduler: "SchedulerBase | None" = None) -> Trigger:
        return Trigger(scheduler or get_default_scheduler(), wpilib.RobotState.is_teleop_enabled)

    @staticmethod
    def disabled(scheduler: "SchedulerBase | None" = None) -> Trigger:
        return Trigger(scheduler or get_default_scheduler(), wpilib.RobotState.is_disabled)

    @staticmethod
    def utility(scheduler: "SchedulerBase | None" = None) -> Trigger:
        return Trigger(scheduler or get_default_scheduler(), wpilib.RobotState.is_utility_enabled)
