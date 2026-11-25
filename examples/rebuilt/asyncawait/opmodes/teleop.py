import wpilib
from wpilib.opmodes import teleop

import constants as C


@teleop(name="DefaultTeleop")
class DefaultTeleop(wpilib.OpMode):
    def __init__(self, robot):
        super().__init__()
        c = robot.controller
        robot.swerve_drive.set_default_command(robot.swerve_drive.driver_control(c))
        c.face_up().on_true(robot.intake.intake())
        c.face_down().on_true(robot.intake.stow())
        c.face_right().while_true(robot.intake.agitate())
        c.right_stick().while_true(
            robot.swerve_drive.aim_assist(
                c, robot.pose_estimator.get_estimated_pose,
                lambda: C.target_hub() or robot.pose_estimator.get_estimated_pose()))
