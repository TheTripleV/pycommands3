"""Run one example robot under one variant and print a JSON result.

usage: python run_robot.py EXAMPLE STYLE RUNTIME
"""

import importlib
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def main(example: str, style: str, runtime: str) -> int:
    project = os.path.join(ROOT, "examples", example, style)
    sys.path.insert(0, project)
    sys.path.insert(0, HERE)
    if runtime == "aio":
        # Example code imports commands3.custom.<style>; run it on the asyncio runtime instead.
        sys.modules[f"commands3.custom.{style}"] = importlib.import_module(f"commands3.aio.{style}")
    import scenarios
    from sim import SimRobot

    robot_mod = importlib.import_module("robot")
    sim = SimRobot(robot_mod.Robot)
    try:
        result = getattr(scenarios, example)(sim)
    finally:
        sim.stop()
    from commands3._common.registry import get_default_scheduler

    result["scheduler_class"] = type(get_default_scheduler()).__module__
    print("RESULT " + json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(*sys.argv[1:4]))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
