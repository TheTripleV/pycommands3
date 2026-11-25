import telemetry
import wpilib

from commands3.custom.asyncawait_noco import Command, co, Mechanism

from constants import HatchConstants


class HatchMechanism(Mechanism, telemetry.TelemetryLoggable):
    def __init__(self):
        Mechanism.__init__(self)
        telemetry.TelemetryLoggable.__init__(self)
        self.hatch_solenoid = wpilib.DoubleSolenoid(
            wpilib.CANPort.CAN_S0, HatchConstants.HATCH_SOLENOID_MODULE,
            wpilib.PneumaticsModuleType.CTRE_PCM, *HatchConstants.HATCH_SOLENOID_PORTS)

    def grab_hatch_command(self) -> Command:
        async def body():
            self.hatch_solenoid.set(wpilib.DoubleSolenoid.Value.FORWARD)
            await co.park()

        return self.run(body).named("Grab Hatch")

    def release_hatch_command(self) -> Command:
        async def body():
            self.hatch_solenoid.set(wpilib.DoubleSolenoid.Value.REVERSE)
            await co.park()

        return self.run(body).named("Release Hatch")

    def log_to(self, table) -> None:
        table.log("extended", self.hatch_solenoid.get() == wpilib.DoubleSolenoid.Value.FORWARD)
