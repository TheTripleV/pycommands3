"""Port of wpilibjExamples templates/commandv3skeleton (Epilogue logging omitted: Java-only)."""

import wpilib

from commands3.custom.asyncawait import Scheduler


class Robot(wpilib.OpModeRobot):
    def __init__(self):
        super().__init__()

    def robot_periodic(self):
        Scheduler.get_default().run()
