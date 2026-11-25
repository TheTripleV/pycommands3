"""Buttons/controllers against the real HAL simulation (DriverStationSim)."""

import ntcore
import pytest
import wpilib
from wpilib.simulation import DriverStationSim

import commands3.custom.asyncawait as v
from commands3.button import (
    CommandGamepad,
    CommandGenericHID,
    CommandJoystick,
    CommandXboxController,
    InternalButton,
    JoystickButton,
    NetworkButton,
    POVButton,
    RobotModeTriggers,
)

PORT = 2


@pytest.fixture
def sched():
    s = v.Scheduler.create_independent_scheduler()
    v.Scheduler.set_default(s)
    DriverStationSim.reset_data()
    yield s
    DriverStationSim.reset_data()


def press(button=None, value=True, axis=None, axis_value=0.0, pov=None):
    DriverStationSim.set_joystick_buttons_maximum_index(PORT, 26)
    DriverStationSim.set_joystick_axes_maximum_index(PORT, 6)
    DriverStationSim.set_joystick_povs_maximum_index(PORT, 1)
    if button is not None:
        DriverStationSim.set_joystick_button(PORT, button, value)
    if axis is not None:
        DriverStationSim.set_joystick_axis(PORT, axis, axis_value)
    if pov is not None:
        DriverStationSim.set_joystick_pov(PORT, 0, pov)
    DriverStationSim.notify_new_data()


def parked(name):
    return v.Command.no_requirements(v.Coroutine.park).named(name)


def test_xbox_button_triggers_command(sched):
    xbox = CommandXboxController(sched, PORT)
    cmd = parked("A held")
    xbox.a().while_true(cmd)
    sched.run()
    assert not sched.is_running(cmd)
    press(int(wpilib.XboxController.Button.A.value))
    sched.run()
    assert sched.is_running(cmd)
    press(int(wpilib.XboxController.Button.A.value), False)
    sched.run()
    assert not sched.is_running(cmd)


def test_trigger_axis_threshold(sched):
    xbox = CommandXboxController(sched, PORT)
    cmd = parked("RT")
    xbox.right_trigger(0.3).on_true(cmd)
    press(axis=int(wpilib.XboxController.Axis.RIGHT_TRIGGER.value), axis_value=0.5)
    sched.run()
    assert sched.is_running(cmd)
    assert xbox.get_right_trigger() == pytest.approx(0.5, abs=0.02)  # sim quantizes axes


def test_triggers_are_cached_per_port(sched):
    a = CommandXboxController(sched, PORT)
    b = CommandGamepad(sched, PORT)
    assert a.get_hid() is b.get_hid()
    assert a.a() is a.a()
    assert a.get_hid().button(3) is b.button(3)


def test_pov(sched):
    hid = CommandGenericHID.get_command_generic_hid(sched, PORT)
    cmd = parked("POV")
    hid.pov_right().on_true(cmd)
    press(pov=wpilib.POVDirection.RIGHT)
    sched.run()
    assert sched.is_running(cmd)


def test_joystick_and_wrappers(sched):
    js = CommandJoystick(sched, PORT)
    cmd = parked("Trigger")
    js.trigger().on_true(cmd)
    press(int(wpilib.Joystick.ButtonType.TRIGGER_BUTTON.value))
    sched.run()
    assert sched.is_running(cmd)
    assert js.get_x_channel() == js.get_joystick().get_x_channel()  # delegated


def test_joystick_button_and_pov_button(sched):
    hid = wpilib.DriverStation.get_generic_hid(PORT)
    c1, c2 = parked("jb"), parked("pov")
    JoystickButton(hid, 4).on_true(c1)
    POVButton(hid, wpilib.POVDirection.UP).on_true(c2)
    press(4, pov=wpilib.POVDirection.UP)
    sched.run()
    assert sched.is_running(c1) and sched.is_running(c2)


def test_internal_button(sched):
    b = InternalButton()
    cmd = parked("internal")
    b.while_true(cmd)
    sched.run()
    assert not sched.is_running(cmd)
    b.set_pressed(True)
    sched.run()
    assert sched.is_running(cmd)
    b.set_inverted(True)
    sched.run()
    assert not sched.is_running(cmd)


def test_network_button(sched):
    inst = ntcore.NetworkTableInstance.create()
    inst.start_local()
    try:
        pub = inst.get_table("T").get_boolean_topic("go").publish()
        cmd = parked("nt")
        NetworkButton(inst, "T", "go").on_true(cmd)
        pub.set(True)
        sched.run()
        # start_local() counts as connected
        assert sched.is_running(cmd) == inst.is_connected()
    finally:
        ntcore.NetworkTableInstance.destroy(inst)


def test_robot_mode_triggers(sched, monkeypatch):
    # RobotState reports UNKNOWN until the robot framework observes program start, which
    # robotpy only does inside start_competition(); full-robot coverage is in tests/integration.
    teleop = {"on": False}
    monkeypatch.setattr(wpilib.RobotState, "is_teleop_enabled", lambda: teleop["on"])
    cmd = parked("teleop")
    RobotModeTriggers.teleop().while_true(cmd)
    sched.run()
    assert not sched.is_running(cmd)
    teleop["on"] = True
    sched.run()
    assert sched.is_running(cmd)


def test_all_generated_controllers_construct(sched):
    import commands3.button as b

    for name in b.__all__:
        if name.startswith("Command") and name.endswith("Controller") and name != "CommandGenericHID":
            ctrl = getattr(b, name)(sched, PORT)
            assert ctrl.get_hid().get_scheduler() is sched


@pytest.mark.parametrize("make", [CommandXboxController, CommandJoystick, CommandGenericHID])
def test_joystick_and_pov_buttons_accept_command_wrappers(sched, make):
    ctrl = make(sched, PORT)
    btn_cmd, pov_cmd = parked("Button"), parked("POV")
    JoystickButton(ctrl, 1, scheduler=sched).on_true(btn_cmd)
    POVButton(ctrl, wpilib.POVDirection.LEFT, scheduler=sched).on_true(pov_cmd)
    press(1, value=False, pov=wpilib.POVDirection.CENTER)
    sched.run()
    assert not sched.is_running(btn_cmd) and not sched.is_running(pov_cmd)
    press(1, pov=wpilib.POVDirection.LEFT)
    sched.run()
    assert sched.is_running(btn_cmd) and sched.is_running(pov_cmd)


def test_trigger_caches_are_per_event_loop(sched):
    hid = CommandGenericHID(sched, PORT)
    loop_a, loop_b = v.EventLoop(), v.EventLoop()
    assert hid.button(1, loop_a) is hid.button(1, loop_a)
    assert hid.button(1, loop_a) is not hid.button(1, loop_b)
    assert hid.axis_greater_than(0, 0.5, loop_a) is not hid.axis_greater_than(0, 0.5, loop_b)


def test_shared_hid_cache_does_not_keep_schedulers_alive():
    import gc
    import weakref

    s = v.Scheduler.create_independent_scheduler()
    CommandXboxController(s, PORT).a().on_true(parked("A"))
    assert CommandGenericHID.get_command_generic_hid(s, PORT) is \
        CommandGenericHID.get_command_generic_hid(s, PORT)
    ref = weakref.ref(s)
    s.close()
    del s
    gc.collect()
    assert ref() is None
