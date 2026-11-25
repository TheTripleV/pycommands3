"""Port of SchedulerPriorityLevelTests.java."""

import pytest

from commands3._common.api import (
    DEFAULT_PRIORITY,
    Canceled,
    Command,
    ForkFailure,
    Interrupted,
    LowerPriorityThanRunningCommand,
    Trigger,
)
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism, PriorityCommand, Ref, assert_event

SINGLE = ["fork", "await", "await_all", "await_any"]
MULTI = ["fork", "await_all", "await_any"]


def test_higher_priority_cancels(scheduler):
    subsystem = DummyMechanism("Subsystem", scheduler)
    lower = PriorityCommand(-1000, subsystem)
    higher = PriorityCommand(1000, subsystem)
    scheduler.schedule(lower)
    scheduler.run()
    assert scheduler.is_running(lower)
    scheduler.schedule(higher)
    scheduler.run()
    assert scheduler.is_running(higher)
    assert not scheduler.is_running(lower)


def test_lower_priority_does_not_cancel(scheduler):
    subsystem = DummyMechanism("Subsystem", scheduler)
    lower = PriorityCommand(-1000, subsystem)
    higher = PriorityCommand(1000, subsystem)
    scheduler.schedule(higher)
    scheduler.run()
    assert scheduler.is_running(higher)
    scheduler.schedule(lower)
    scheduler.run()
    assert scheduler.is_running(higher)
    assert not scheduler.is_scheduled_or_running(lower)


def test_same_priority_cancels(scheduler):
    subsystem = DummyMechanism("Subsystem", scheduler)
    first = PriorityCommand(512, subsystem)
    second = PriorityCommand(512, subsystem)
    scheduler.schedule(first)
    scheduler.run()
    assert scheduler.is_running(first)
    scheduler.schedule(second)
    scheduler.run()
    assert scheduler.is_running(second)
    assert not scheduler.is_running(first)


def test_child_inherits_higher_parent_priority(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    higher = PriorityCommand(200, mech)
    low_child = PriorityCommand(-1000, mech)

    async def body(co: Coroutine):
        await co.await_(low_child)

    parent = Command.no_requirements(body).with_priority(1000).named("Parent")
    scheduler.schedule(higher)
    scheduler.schedule(parent)
    scheduler.run()
    assert scheduler.is_running(parent)
    assert scheduler.is_running(low_child)
    assert not scheduler.is_running(higher)


def test_inner_default_command_inherits_higher_parent_priority(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    higher = PriorityCommand(200, mech)
    low_child = PriorityCommand(-1000, mech)

    async def body(co: Coroutine):
        mech.set_default_command(low_child)
        await co.park()

    parent = Command.no_requirements(body).with_priority(1000).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent, low_child]
    result = scheduler.schedule(higher)
    assert isinstance(result, LowerPriorityThanRunningCommand)
    assert result.already_running is low_child
    assert result.command is higher
    assert scheduler.get_running_commands() == [parent, low_child]


def test_inner_trigger_command_inherits_higher_parent_priority(scheduler, events):
    mech = DummyMechanism("Mechanism", scheduler)
    higher = PriorityCommand(200, mech)
    low_child = PriorityCommand(-1000, mech)
    trigger = Trigger(scheduler, lambda: True)

    async def body(co: Coroutine):
        trigger.retry_while_true(low_child)
        await co.park()

    parent = Command.no_requirements(body).with_priority(1000).named("Parent")
    scheduler.schedule(higher)
    scheduler.run()
    scheduler.schedule(parent)
    scheduler.run()
    scheduler.run()
    assert scheduler.get_running_commands() == [parent, low_child]
    assert_event(events, Interrupted,
                 lambda i: i.interrupter is low_child and i.command is higher)


def test_conflicting_commands_with_priority_inheritance(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    child1, child2 = PriorityCommand(0, mech), PriorityCommand(0, mech)

    async def b1(co: Coroutine):
        await co.await_(child1)

    async def b2(co: Coroutine):
        await co.await_(child2)

    parent1 = Command.no_requirements(b1).with_priority(2000).named("Parent1")
    parent2 = Command.no_requirements(b2).with_priority(1000).named("Parent2")
    scheduler.schedule(parent1)
    scheduler.schedule(parent2)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent1, child1]


def test_conflicting_commands_with_priority_inheritance2(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    child1, child2 = PriorityCommand(0, mech), PriorityCommand(0, mech)

    async def b1(co: Coroutine):
        await co.await_(child1)

    async def b2(co: Coroutine):
        await co.await_(child2)

    parent1 = Command.no_requirements(b1).with_priority(1000).named("Parent1")
    parent2 = Command.no_requirements(b2).with_priority(2000).named("Parent2")
    scheduler.schedule(parent1)
    scheduler.schedule(parent2)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent2, child2]


def _is_lptrc(failure, cmd, running):
    return (isinstance(failure, LowerPriorityThanRunningCommand)
            and failure.command is cmd and failure.already_running is running)


@pytest.mark.parametrize("op", SINGLE)
def test_low_priority_child_cancels_parent(scheduler, events, op):
    subsystem = DummyMechanism("Subsystem", scheduler)
    high = PriorityCommand(512, subsystem)
    default = PriorityCommand(DEFAULT_PRIORITY, subsystem)

    async def body(co: Coroutine):
        if op == "fork":
            co.fork(default)
        elif op == "await":
            await co.await_(default)
        elif op == "await_all":
            await co.await_all(default)
        else:
            await co.await_any(default)

    parent = Command.no_requirements(body).named("Parent")
    scheduler.schedule(high)
    scheduler.schedule(parent)
    scheduler.run()
    assert not scheduler.is_running(parent)
    assert_event(events, Canceled, lambda c: c.command is parent)
    assert_event(events, ForkFailure,
                 lambda e: e.command is parent and len(e.failures) == 1
                 and _is_lptrc(e.failures[0], default, high))


def _assert_fork_failure(scheduler, op, already_running, commands, expected_failures):
    async def body(co: Coroutine):
        co.set_cancel_on_fork_failure(False)
        if op == "fork":
            result = co.fork(*commands)
        elif op == "await":
            result = await co.await_(commands[0])
        elif op == "await_all":
            result = await co.await_all(*commands)
        else:
            result = await co.await_any(*commands)
        assert result.failed(), "Fork result should be a failure"
        assert [f.command for f in result.get_failed_commands()] == expected_failures
        assert not any(f.successful() for f in result.get_failed_commands())
        await co.park()

    parent = Command.no_requirements(body).named("Parent")
    scheduler.schedule(already_running)
    scheduler.schedule(parent)
    scheduler.run()
    assert scheduler.is_running(parent), "Parent command should still be running"
    assert scheduler.is_running(already_running)
    for c in commands:
        assert not scheduler.is_scheduled_or_running(c), f"{c.name()} should not have been scheduled"


@pytest.mark.parametrize("op", SINGLE)
def test_low_priority_child_gives_failure_object(scheduler, op):
    subsystem = DummyMechanism("Subsystem", scheduler)
    high = PriorityCommand(512, subsystem)
    default = PriorityCommand(DEFAULT_PRIORITY, subsystem)
    _assert_fork_failure(scheduler, op, high, [default], [default])


@pytest.mark.parametrize("op", MULTI)
def test_low_priority_child_with_schedulable_sibling_gives_failure_object(scheduler, op):
    busy = DummyMechanism("Busy Subsystem", scheduler)
    idle = DummyMechanism("Idle Subsystem", scheduler)
    high = PriorityCommand(512, busy)
    unschedulable = PriorityCommand(DEFAULT_PRIORITY, busy)
    schedulable = PriorityCommand(DEFAULT_PRIORITY, idle)
    _assert_fork_failure(scheduler, op, high, [unschedulable, schedulable], [unschedulable])


@pytest.mark.parametrize("op", SINGLE)
def test_low_priority_child_cancels_entire_composition(scheduler, events, op):
    subsystem = DummyMechanism("Subsystem", scheduler)
    high = PriorityCommand(512, subsystem)
    unschedulable = PriorityCommand(DEFAULT_PRIORITY, subsystem)
    grandchild = Command.no_requirements(Coroutine.park).named("Grandchild")

    async def child_body(co: Coroutine):
        co.fork(grandchild)
        await co.park()

    child = Command.no_requirements(child_body).named("Child")

    async def current_body(co: Coroutine):
        co.fork(child)
        if op == "fork":
            co.fork(unschedulable)
        elif op == "await":
            await co.await_(unschedulable)
        elif op == "await_all":
            await co.await_all(unschedulable)
        else:
            await co.await_any(unschedulable)

    current = Command.no_requirements(current_body).named("Current")

    async def parent_body(co: Coroutine):
        await co.await_(current)

    parent = Command.no_requirements(parent_body).named("Parent")

    async def grandparent_body(co: Coroutine):
        await co.await_(parent)

    grandparent = Command.no_requirements(grandparent_body).named("Grandparent")
    scheduler.schedule(high)
    scheduler.schedule(grandparent)
    scheduler.run()
    assert scheduler.is_running(high)
    assert_event(events, ForkFailure,
                 lambda e: e.command is current and len(e.failures) == 1
                 and _is_lptrc(e.failures[0], unschedulable, high))
    for command in (grandparent, parent, current, child, grandchild):
        assert not scheduler.is_scheduled_or_running(command), f"{command.name()} should have been canceled"
        assert_event(events, Canceled, lambda e: e.command is command,
                     f"{command.name()} should have received a cancellation event")


@pytest.mark.parametrize("op", MULTI)
def test_high_priority_grandchild_with_low_priority_child(scheduler, op):
    mech = DummyMechanism("Mech", scheduler)
    grandchild = PriorityCommand(1000, mech)

    async def child1_body(co: Coroutine):
        await co.await_(grandchild)

    child1 = Command.no_requirements(child1_body).named("Child1")
    child2 = PriorityCommand(0, mech)
    checked = Ref(False)

    async def body(co: Coroutine):
        co.set_cancel_on_fork_failure(False)
        if op == "fork":
            failure = co.fork(child1, child2)
        elif op == "await_all":
            failure = await co.await_all(child1, child2)
        else:
            failure = await co.await_any(child1, child2)
        assert failure.failed(), "Forking operation should have failed"
        fails = failure.get_failed_commands()
        assert len(fails) == 1
        assert isinstance(fails[0], LowerPriorityThanRunningCommand)
        assert fails[0].command is child2
        assert fails[0].already_running is grandchild
        checked.set(True)

    scheduler.schedule(Command.no_requirements(body).named("Parent"))
    scheduler.run()
    assert checked.get()
