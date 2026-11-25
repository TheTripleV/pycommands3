import wpimath
from wpimath import Pose2d, Rectangle2d, Rotation2d, SwerveModulePosition

from constants import KINEMATICS


class PoseEstimator:
    def __init__(self):
        self._estimator = wpimath.SwerveDrive4PoseEstimator(
            KINEMATICS, Rotation2d(),
            (SwerveModulePosition(), SwerveModulePosition(), SwerveModulePosition(),
             SwerveModulePosition()),
            Pose2d())  # field center, facing the blue alliance wall

    def odometry_update(self, gyro_heading: Rotation2d, module_positions) -> None:
        self._estimator.update(gyro_heading, module_positions)

    def vision_update(self, estimated_pose: Pose2d, timestamp: float) -> None:
        self._estimator.add_vision_measurement(estimated_pose, timestamp)

    def get_estimated_pose(self) -> Pose2d:
        return self._estimator.get_estimated_position()

    def in_zone(self, bounds: Rectangle2d) -> bool:
        return bounds.contains(self._estimator.get_estimated_position().translation())
