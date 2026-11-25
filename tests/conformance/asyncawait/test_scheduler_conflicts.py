"""Port of SchedulerConflictTests.java."""

import pytest

from commands3._common.api import Command
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism, Ref


def test_compositions_cannot_await_conflicting_commands(scheduler):
    mech = DummyMechanism("The Mechanism", scheduler)

    async def body(co: Coroutine):
        await co.await_all(mech.run(Coroutine.park).named("First"),
                           mech.run(Coroutine.park).named("Second"))

    scheduler.schedule(Command.no_requirements(body).named("Group"))
    with pytest.raises(ValueError) as e:
        scheduler.run()
    assert str(e.value) == ("Commands running in parallel cannot share requirements: "
                            "First and Second both require The Mechanism")


def test_inner_command_may_interrupt_other_inner_command(scheduler):
    mechanism = DummyMechanism("The mechanism", scheduler)
    first_ran, second_ran = Ref(False), Ref(False)

    async def first_body(c: Coroutine):
        first_ran.set(True)
        await c.park()

    async def second_body(c: Coroutine):
        second_ran.set(True)
        await c.park()

    first = mechanism.run(first_body).named("First")
    second = mechanism.run(second_body).named("Second")

    async def group_body(co: Coroutine):
        co.fork(first)
        co.fork(second)
        await co.park()

    group = Command.no_requirements(group_body).named("Group")
    scheduler.schedule(group)
    scheduler.run()
    assert first_ran.get()
    assert second_ran.get()
    assert not scheduler.is_scheduled_or_running(first)
    assert scheduler.is_running(second)
    assert scheduler.is_running(group)


def test_nested_one_shot_compositions_all_run_in_one_cycle(scheduler):
    runs = Ref(0)

    def make_one_shot():
        return Command.no_requirements(lambda c: runs.increment_and_get()).named("One Shot")

    def three(co3: Coroutine):
        co3.fork(make_one_shot())

    def two(co2: Coroutine):
        co2.fork(make_one_shot())
        co2.fork(Command.no_requirements(three).named("3"))

    def body(co: Coroutine):
        co.fork(make_one_shot())
        co.fork(make_one_shot())
        co.fork(Command.no_requirements(lambda inner: inner.fork(make_one_shot())).named("Inner"))
        co.fork(Command.no_requirements(two).named("2"))

    command = Command.no_requirements(body).named("Command")
    scheduler.schedule(command)
    scheduler.run()
    assert runs.get() == 5, "All oneshot commands should have run"
    assert not scheduler.is_running(command)


def test_child_conflicts_with_higher_priority_top_level(scheduler):
    mechanism = DummyMechanism("mechanism", scheduler)
    top = mechanism.run(Coroutine.park).with_priority(10).named("Top")
    child = mechanism.run(Coroutine.park).named("Child")

    async def body(co: Coroutine):
        await co.await_(child)

    parent = Command.no_requirements(body).named("Parent")
    scheduler.schedule(top)
    scheduler.schedule(parent)
    scheduler.run()
    assert scheduler.is_running(top)
    assert not scheduler.is_running(child)
    assert not scheduler.is_running(parent)


def test_child_conflicts_with_lower_priority_top_level(scheduler):
    mechanism = DummyMechanism("mechanism", scheduler)
    top = mechanism.run(Coroutine.park).with_priority(-10).named("Top")
    child = mechanism.run(Coroutine.park).named("Child")

    async def body(co: Coroutine):
        await co.await_(child)

    parent = Command.no_requirements(body).named("Parent")
    scheduler.schedule(top)
    scheduler.schedule(parent)
    scheduler.run()
    assert not scheduler.is_running(top)
    assert scheduler.is_running(child)
    assert scheduler.is_running(parent)
