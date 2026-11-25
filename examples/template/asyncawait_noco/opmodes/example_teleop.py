import wpilib
from wpilib.opmodes import teleop


@teleop
class ExampleTeleop(wpilib.OpMode):
    def __init__(self, robot):
        super().__init__()
        robot.example_controller.right_stick().while_true(
            robot.example_mechanism.example_command())
