from wpimath import ChassisVelocities


class PathFollower:
    """Stub path follower: an empty path that is immediately done."""

    @staticmethod
    def load(path_name: str) -> "PathFollower":
        return PathFollower()

    def next(self) -> ChassisVelocities:
        return ChassisVelocities()

    def is_done(self) -> bool:
        return True
