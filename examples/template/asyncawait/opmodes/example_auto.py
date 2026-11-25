import wpilib
from wpilib.opmodes import autonomous


@autonomous
class ExampleAuto(wpilib.OpMode):
    def __init__(self, robot):
        super().__init__()
        # Bindings made while an opmode is active are scoped to that opmode.
        robot.example_mechanism.example_condition.while_true(
            robot.example_mechanism.example_command())
