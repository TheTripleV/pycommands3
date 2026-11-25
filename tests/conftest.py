import importlib
import time

import pytest

from commands3._common import robot_state, time as c3time
from commands3._common.registry import set_default_scheduler

RUNTIMES = ["custom", "aio"]


@pytest.fixture(autouse=True)
def _robot_state():
    """CommandTestBase.initOpmodeFetcher: mutable mock robot state, reset per test."""
    fetcher = robot_state.MockRobotStateFetcher()
    robot_state.set_fetcher(fetcher)
    c3time.set_time_source(time.monotonic_ns)
    yield fetcher
    robot_state.set_fetcher(None)
    c3time.set_time_source(None)
    set_default_scheduler(None)


@pytest.fixture
def robot(_robot_state):
    return _robot_state


@pytest.fixture(params=RUNTIMES)
def runtime(request):
    return request.param


@pytest.fixture
def variant(runtime, style):
    return importlib.import_module(f"commands3.{runtime}.{style}")


@pytest.fixture
def scheduler(variant):
    s = variant.Scheduler.create_independent_scheduler()
    yield s
    try:
        s.close()
    except Exception:
        pass  # tests may leave commands in deliberately broken states


@pytest.fixture
def events(scheduler):
    evs = []
    scheduler.add_event_listener(evs.append)
    return evs


class FakeClock:
    def __init__(self, start_ns: int = 0):
        self.ns = start_ns

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> None:
        self.ns += int(round(seconds * 1e9))

    def set(self, seconds: float) -> None:
        self.ns = int(round(seconds * 1e9))


@pytest.fixture
def clock():
    c = FakeClock()
    c3time.set_time_source(c)
    return c
