"""Scripted drives of each example robot. Each returns {"trace": [...], "facts": {...}}.

Traces contain only logical state (command names, setpoints, states), so they must be
identical across all eight variants.
"""

import hal
import wpilib
from wpilib.simulation import DriverStationSim as DS, EncoderSim

from commands3._common.registry import get_default_scheduler

GB = wpilib.Gamepad.Button
GA = wpilib.Gamepad.Axis


def running():
    return sorted(c.name() for c in get_default_scheduler().get_running_commands())


def switch_opmode(sim, name, cycles=2):
    sim.enable(False)
    sim.step(cycles)
    sim.select_opmode(name)
    sim.step(cycles)
    sim.enable(True)
    sim.step(cycles)


# ------------------------------------------------------------------------- template ---
def template(sim):
    r, facts, trace = sim.robot, {}, []
    stick = int(GB.RIGHT_STICK.value)
    sim.step(3)
    switch_opmode(sim, "ExampleTeleop")
    sim.gamepad(1, buttons={stick: True})
    sim.step(5, each=lambda: trace.append(running()))
    facts["teleop_runs"] = r.example_mechanism.run_count
    sim.gamepad(1, buttons={stick: False})
    sim.step(2)
    facts["after_release"] = running()
    switch_opmode(sim, "ExampleAuto")
    sim.gamepad(1, buttons={stick: True})
    sim.step(3)
    facts["auto_with_stick_pressed"] = running()  # teleop binding is opmode-scoped
    return {"trace": trace, "facts": facts}


def skeleton(sim):
    sim.step(5)
    switch_opmode(sim, "Teleop") if any(o.name == "Teleop" for o in DS.get_opmode_options()) else None
    sim.step(20)
    return {"trace": [], "facts": {"ran": True, "running": running()}}


# ------------------------------------------------------------------------- hatchbot ---
def hatchbot(sim):
    r, facts, trace = sim.robot, {}, []
    drive = r.robot_drive
    sim.step(2)
    sim.mode(hal.RobotMode.TELEOPERATED)
    sim.enable()
    sim.gamepad(0, axes={int(GA.LEFT_Y.value): -0.5})
    sim.step(3)
    facts["arcade_left"] = round(drive.left_output, 2)
    sim.gamepad(0, buttons={int(GB.FACE_RIGHT.value): True})
    sim.step(2)
    facts["after_grab"] = str(r.hatch_mechanism.hatch_solenoid.get())
    sim.gamepad(0, buttons={int(GB.FACE_RIGHT.value): False, int(GB.FACE_LEFT.value): True})
    sim.step(2)
    facts["after_release"] = str(r.hatch_mechanism.hatch_solenoid.get())
    sim.gamepad(0, buttons={int(GB.RIGHT_BUMPER.value): True})
    sim.step(3)
    facts["half_speed_left"] = round(drive.left_output, 2)
    sim.gamepad(0, buttons={int(GB.RIGHT_BUMPER.value): False, int(GB.FACE_LEFT.value): False},
                axes={int(GA.LEFT_Y.value): 0.0})
    sim.step(2)

    # Autonomous "Simple Auto": drive 60 in at 0.5 with encoders driven by a toy plant.
    left, right = EncoderSim(drive.left_encoder), EncoderSim(drive.right_encoder)
    dist = {"d": 0.0}

    def physics():
        dist["d"] += drive.left_output * 100 * 0.02  # 100 in/s at full throttle
        left.set_distance(dist["d"])
        right.set_distance(dist["d"])
        trace.append((round(drive.left_output, 2), running()))

    sim.enable(False)
    sim.step(2)
    sim.mode(hal.RobotMode.AUTONOMOUS)
    sim.enable(True)
    sim.step(150, each=physics)
    facts["auto_distance"] = round(dist["d"], 1)
    facts["auto_running_at_end"] = running()
    return {"trace": trace, "facts": facts}


# -------------------------------------------------------------------------- rebuilt ---
def rebuilt(sim):
    from stubs.smart_motor import ExampleSmartMotorController

    motors = ExampleSmartMotorController.registry
    WRIST, ROLLER = 21, 20
    facts, trace = {}, []

    def wrist():
        m = motors[WRIST]
        return (m.mode.name if m.mode else "THROTTLE", round(m.setpoint, 3))

    sim.step(3)
    facts["idle"] = running()
    switch_opmode(sim, "DefaultTeleop")
    sim.gamepad(1, buttons={int(GB.FACE_UP.value): True})
    sim.step(2)
    sim.gamepad(1, buttons={int(GB.FACE_UP.value): False})
    sim.step(2)
    facts["intake"] = running()
    facts["intake_wrist"] = wrist()
    facts["intake_roller"] = round(motors[ROLLER].setpoint, 1)
    sim.gamepad(1, buttons={int(GB.FACE_RIGHT.value): True})
    sim.step(40, each=lambda: trace.append(wrist()))
    sim.gamepad(1, buttons={int(GB.FACE_RIGHT.value): False})
    sim.step(2)
    facts["after_agitate"] = running()

    DS.set_alliance_station_id(hal.AllianceStationID.RED_1)
    switch_opmode(sim, "SweepAuto")
    sim.step(5)
    facts["auto"] = running()
    return {"trace": trace, "facts": facts}


# ------------------------------------------------------------------- superstructure ---
SCRIPT = {
    5: {"intake": True}, 40: {"intake": False},
    45: {"score": True}, 52: {"score": False},     # let go mid-way: superstructure backs out
    80: {"score": True}, 200: {"score": False},
}


def superstructure(sim):
    r, trace = sim.robot, []
    ss, p = r.superstructure, r.plant
    lb, rb = int(GB.LEFT_BUMPER.value), int(GB.RIGHT_BUMPER.value)
    held = {"intake": False, "score": False}
    sim.step(3)
    switch_opmode(sim, "Superstructure Teleop")
    for tick in range(220):
        if tick in SCRIPT:
            held.update(SCRIPT[tick])
            sim.gamepad(0, buttons={lb: held["intake"], rb: held["score"]})
        sim.step(1)
        trace.append((tick, ss.state_name(), ss.elevator.state, round(p.elevator_volts, 2),
                      round(p.arm_volts, 2), p.roller_volts, p.has_piece))
    transitions = []
    for row in trace:
        if not transitions or transitions[-1][1:3] != list(row[1:3]):
            transitions.append([row[0], row[1], row[2]])
    return {"trace": trace, "facts": {"transitions": transitions}}
