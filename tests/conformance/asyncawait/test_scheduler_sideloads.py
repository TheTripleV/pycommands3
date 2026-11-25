"""Port of SchedulerSideloadFunctionTests.java."""

import pytest

from commands3._common.api import Command, Trigger
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism, PriorityCommand, Ref


def test_sideload_throwing_exception(scheduler):
    def boom(co: Coroutine):
        raise RuntimeError("Bang!")

    scheduler.sideload(boom)
    with pytest.raises(RuntimeError, match="Bang!"):
        scheduler.run()


def test_periodic_sideload(scheduler):
    count = Ref(0)
    scheduler.add_periodic(count.increment_and_get)
    assert count.get() == 0
    scheduler.run()
    assert count.get() == 1
    scheduler.run()
    assert count.get() == 2


def test_multiple_periodic_sideloads(scheduler):
    n = 4
    count = Ref(0)
    for _ in range(n):
        scheduler.add_periodic(count.increment_and_get)
    assert count.get() == 0
    scheduler.run()
    assert count.get() == n
    scheduler.run()
    assert count.get() == n * 2


def test_sideload_scheduling_command(scheduler):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.sideload(lambda co: co.fork(command))
    scheduler.run()
    assert scheduler.is_running(command), "command should have started and outlasted the sideload"


def test_child_command_cannot_escape_via_sideload(scheduler):
    sideload_ran = Ref(False)
    child = Command.no_requirements(Coroutine.park).named("Child")

    def sideload_body(sideload_co: Coroutine):
        sideload_ran.set(True)
        sideload_co.fork(child)

    def parent_body(parent_co: Coroutine):
        scheduler.sideload(sideload_body)

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert not scheduler.is_scheduled_or_running(parent)
    assert not scheduler.is_scheduled_or_running(child)
    assert not sideload_ran.get()
    scheduler.run()
    assert not sideload_ran.get(), "the sideload should not have run"
    assert not scheduler.is_running(child)


def test_sideload_canceling_command(scheduler):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.schedule(command)
    scheduler.run()
    assert scheduler.is_running(command)
    scheduler.sideload(lambda co: scheduler.cancel(command))
    assert scheduler.is_running(command), "sideload should not have run yet"
    scheduler.run()
    assert not scheduler.is_running(command)


def test_sideload_affects_state_for_trigger_in_same_cycle(scheduler):
    signal = Ref(False)
    trigger = Trigger(scheduler, signal.get)
    command = Command.no_requirements(Coroutine.park).named("Command")
    trigger.on_true(command)
    scheduler.sideload(lambda co: signal.set(True))
    scheduler.run()
    assert signal.get()
    assert scheduler.is_running(command)


def test_sideload_failing_to_fork_does_not_cancel(scheduler):
    mech = DummyMechanism("Mech", scheduler)
    high = PriorityCommand(100, mech)
    low = PriorityCommand(0, mech)
    reached = Ref(False)

    def body(co: Coroutine):
        result = co.fork(low)
        reached.set(True)
        assert result.failed()
        fails = result.get_failed_commands()
        assert len(fails) == 1
        assert fails[0].command is low

    scheduler.sideload(body)
    scheduler.schedule(high)
    scheduler.run()
    assert reached.get(), "sideload function was terminated before it reached assertions"


async def _ticker(co: Coroutine, log):
    while True:
        log.append("tick")
        await co.yield_()


def test_suspending_sideload_resumes_each_cycle(scheduler):
    """Python addition: a sideload is a full coroutine in every style."""
    log = []
    scheduler.sideload(lambda co: _ticker(co, log))
    for _ in range(3):
        scheduler.run()
    assert log == ["tick"] * 3
