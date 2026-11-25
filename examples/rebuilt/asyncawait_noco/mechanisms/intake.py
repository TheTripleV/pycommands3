"""Intake: a mechanism whose commands drive two sub-mechanisms (cascading ownership)."""

from commands3.custom.asyncawait_noco import LOWEST_PRIORITY, Command, co, Mechanism

import constants as C
from stubs.smart_motor import ExampleSmartMotorController, PIDMode


class IntakeWrist(Mechanism):
    def __init__(self):
        super().__init__("Intake.Wrist")
        self.motor = ExampleSmartMotorController(C.WRIST_MOTOR_ID)

    def stop(self) -> Command:
        return self.run_repeatedly(lambda: self.motor.set_throttle(0.0)).named("Intake.Wrist.Stop")

    def idle(self) -> Command:
        return self.stop()

    def move_to_angle(self, target: float) -> Command:
        tolerance = 0.5 * 3.141592653589793 / 180

        async def body():
            self.motor.set_setpoint(PIDMode.POSITION, target)
            await co.wait_until(lambda: abs(self.motor.get_encoder_distance() - target) <= tolerance)

        return self.run(body).named(f"Intake.Wrist.MoveToAngle[{target:.3f} rad]")

    def hold_angle(self, target: float) -> Command:
        async def body():
            self.motor.set_setpoint(PIDMode.POSITION, target)
            await co.park()

        return self.run(body).named(f"Intake.Wrist.HoldAngle[{target:.3f} rad]")

    def stow(self) -> Command:
        return self.hold_angle(C.WRIST_STOW_ANGLE)

    def down(self) -> Command:
        return self.hold_angle(C.WRIST_DOWN_ANGLE)


class IntakeRoller(Mechanism):
    def __init__(self):
        super().__init__("Intake.Roller")
        self.motor = ExampleSmartMotorController(C.ROLLER_MOTOR_ID)

    def stop(self) -> Command:
        return self.run_repeatedly(lambda: self.motor.set_throttle(0.0)).named("Intake.Roller.Stop")

    def idle(self) -> Command:
        return self.stop()

    def intake(self) -> Command:
        async def body():
            self.motor.set_setpoint(PIDMode.VELOCITY, C.ROLLER_INTAKE_SPEED)
            await co.park()

        return self.run(body).named("Intake.Roller.Intake")

    def expel(self) -> Command:
        async def body():
            self.motor.set_setpoint(PIDMode.VELOCITY, C.ROLLER_EXPULSION_SPEED)
            await co.park()

        return self.run(body).named("Intake.Roller.Expel")


class Intake(Mechanism):
    def __init__(self):
        super().__init__("Intake")
        self.wrist = IntakeWrist()
        self.roller = IntakeRoller()

    def intake(self) -> Command:
        async def body():
            await co.await_all(self.wrist.down(), self.roller.intake())

        return self.run(body).named("Intake.Intake")

    def stop(self) -> Command:
        async def body():
            await co.await_all(self.wrist.stop(), self.roller.stop())

        return self.run(body).with_priority(LOWEST_PRIORITY).named("Intake.Idle")

    def idle(self) -> Command:
        return self.stop()

    def stow(self) -> Command:
        async def body():
            await co.await_all(self.wrist.stow(), self.roller.stop())

        return self.run(body).named("Intake.Stow")

    def agitate(self) -> Command:
        async def body():
            co.fork(self.roller.intake())
            while True:
                await co.await_(self.wrist.move_to_angle(C.WRIST_AGITATE_UP))
                await co.await_(self.wrist.move_to_angle(C.WRIST_AGITATE_DOWN))

        return self.run(body).named("Intake.Agitate")

    def outtake(self) -> Command:
        async def body():
            await co.await_all(self.wrist.down(), self.roller.expel())

        return self.run(body).named("Intake.Outtake")
