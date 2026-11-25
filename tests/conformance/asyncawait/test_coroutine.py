"""Port of CoroutineTest.java."""

import threading

import pytest

from commands3._common.api import Canceled, Command, WaitResult
from commands3._frontends.asyncawait import Coroutine

from .helpers import NullCommand, Ref, assert_event


def test_wait_until_condition_met(scheduler):
    condition = Ref(False)
    result = Ref()

    async def body(co: Coroutine):
        result.set(await co.wait_until(condition.get, 1.0))

    command = Command.no_requirements(body).named("Wait Until Condition Met")
    scheduler.schedule(command)
    scheduler.run()
    assert scheduler.is_running(command)
    condition.set(True)
    scheduler.run()
    assert result.get() is WaitResult.CONDITION_MET
    assert not scheduler.is_running(command)


def test_wait_until_timeout(scheduler, clock):
    result = Ref()

    async def body(co: Coroutine):
        result.set(await co.wait_until(lambda: False, 1.0))

    command = Command.no_requirements(body).named("Wait Until Timeout")
    scheduler.schedule(command)
    scheduler.run()
    assert scheduler.is_running(command)
    clock.set(0.5)
    scheduler.run()
    assert scheduler.is_running(command)
    clock.set(1.1)
    scheduler.run()
    assert result.get() is WaitResult.TIMED_OUT
    assert result.get().timed_out()
    assert not scheduler.is_running(command)


def test_wait_until_immediate_condition_met(scheduler):
    result = Ref()

    async def body(co: Coroutine):
        result.set(await co.wait_until(lambda: True, 1.0))

    command = Command.no_requirements(body).named("Wait Until Immediate Condition Met")
    scheduler.schedule(command)
    scheduler.run()
    assert result.get() is WaitResult.CONDITION_MET
    assert not scheduler.is_running(command)


def test_wait_until_condition_met_exactly_at_timeout(scheduler, clock):
    condition = Ref(False)
    result = Ref()

    async def body(co: Coroutine):
        result.set(await co.wait_until(condition.get, 1.0))

    command = Command.no_requirements(body).named("Wait Until Condition Met Exactly At Timeout")
    scheduler.schedule(command)
    scheduler.run()
    clock.set(1.0)
    condition.set(True)
    scheduler.run()
    assert result.get() is WaitResult.CONDITION_MET
    assert not scheduler.is_running(command)


def test_wait_until_null_param_throws(scheduler):
    checked = Ref(0)

    async def body(co: Coroutine):
        with pytest.raises(TypeError):
            await co.wait_until(None, 1.0)  # type: ignore[arg-type]
        checked.increment_and_get()

    command = Command.no_requirements(body).named("Wait Until Null Param Throws")
    scheduler.schedule(command)
    scheduler.run()
    assert checked.get() == 1
    assert not scheduler.is_running(command)


def test_fork_many(scheduler):
    a, b, c = NullCommand(), NullCommand(), NullCommand()

    async def body(co: Coroutine):
        co.fork(a, b, c)
        await co.park()

    scheduler.schedule(Command.no_requirements(body).named("Fork Many"))
    scheduler.run()
    assert scheduler.is_running(a)
    assert scheduler.is_running(b)
    assert scheduler.is_running(c)


def test_fork_result_await_completion(scheduler):
    signal = Ref(False)

    async def waiting(co: Coroutine):
        await co.wait_until(signal.get)

    waiting_command = Command.no_requirements(waiting).named("Wait For Signal")

    async def parent_body(co: Coroutine):
        fork_result = co.fork(waiting_command)
        await fork_result.await_completion()

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent, waiting_command]
    for _ in range(4):
        scheduler.run()
    assert scheduler.get_running_commands() == [parent, waiting_command]
    signal.set(True)
    scheduler.run()
    assert scheduler.get_running_commands() == []


def test_fork_result_await_completion_one_shot(scheduler):
    ran = Ref(False)
    one_shot = Command.no_requirements(lambda co: None).named("OneShot")

    async def parent_body(co: Coroutine):
        fork_result = co.fork(one_shot)
        assert fork_result.successful()
        await fork_result.await_completion()
        ran.set(True)

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert ran.get()
    assert scheduler.get_running_commands() == []


def test_yield_in_synchronized_block(scheduler):
    mutex = threading.RLock()
    i = Ref(0)

    async def body(co: Coroutine):
        while True:
            with mutex:
                i.increment_and_get()
                await co.yield_()

    scheduler.schedule(Command.no_requirements(body).named("Yield In Synchronized Block"))
    scheduler.run()
    assert i.get() == 1


def test_yield_in_lock_body(scheduler):
    lock = threading.Lock()
    i = Ref(0)

    async def body(co: Coroutine):
        while True:
            lock.acquire()
            try:
                i.increment_and_get()
                await co.yield_()
            finally:
                lock.release()

    scheduler.schedule(Command.no_requirements(body).named("Increment In Lock Block"))
    scheduler.run()
    assert i.get() == 1


def test_coroutine_escaping_command(scheduler):
    escapee = Ref()

    async def body(co: Coroutine):
        escapee.set(co)

    scheduler.schedule(Command.no_requirements(body).named("Bad Command"))
    scheduler.run()
    # Java calls co.yield(); every coroutine operation performs the same mounted check, and
    # scheduler() is the one that does so without needing a suspension.
    with pytest.raises(RuntimeError, match="Coroutines can only be used by the command bound to them"):
        escapee.get().scheduler()
    with pytest.raises(RuntimeError, match="Coroutines can only be used by the command bound to them"):
        escapee.get().fork(NullCommand())


def test_using_parent_coroutine_in_child_throws(scheduler):
    async def parent_body(parent_co: Coroutine):
        async def child_body(child_co: Coroutine):
            await parent_co.yield_()

        await parent_co.await_(Command.no_requirements(child_body).named("Child"))

    scheduler.schedule(Command.no_requirements(parent_body).named("Parent"))
    with pytest.raises(RuntimeError, match="Coroutines can only be used by the command bound to them"):
        scheduler.run()


def test_await_any_cleans_up(scheduler):
    first_ran, second_ran, ran_after_await = Ref(False), Ref(False), Ref(False)
    first_inner = Command.no_requirements(lambda c2: first_ran.set(True)).named("First")

    async def second(c2: Coroutine):
        second_ran.set(True)
        await c2.park()

    second_inner = Command.no_requirements(second).named("Second")

    async def outer_body(co: Coroutine):
        await co.await_any(first_inner, second_inner)
        ran_after_await.set(True)
        await co.park()

    outer = Command.no_requirements(outer_body).named("Command")
    scheduler.schedule(outer)
    scheduler.run()
    assert first_ran.get()
    assert second_ran.get()
    assert ran_after_await.get()
    assert scheduler.get_running_commands() == [outer]


def test_await_any_does_not_cancel_rescheduled_command(scheduler, events):
    ran_after_await = Ref(False)
    c1 = Command.no_requirements(Coroutine.park).named("C1")
    c2 = Command.no_requirements(Coroutine.park).named("C2")

    async def parent_body(co: Coroutine):
        await co.await_any(c1, c2)
        ran_after_await.set(True)
        await co.park()

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    scheduler.cancel(c1)
    scheduler.schedule(c1)
    scheduler.run()
    assert ran_after_await.get()
    assert scheduler.get_running_commands() == [parent, c1]
    assert_event(events, Canceled, lambda e: e.command is c2,
                 "C2 should have been canceled by awaitAny")


def test_fork_result_delayed_await_completion_after_completion(scheduler):
    child_ran, parent_done = Ref(False), Ref(False)
    child = Command.no_requirements(lambda co: child_ran.set(True)).named("Child")

    async def parent_body(co: Coroutine):
        fork_result = co.fork(child)
        await co.yield_()
        await co.yield_()
        await fork_result.await_completion()
        parent_done.set(True)

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    scheduler.run()
    assert child_ran.get()
    assert not scheduler.is_running(child)
    scheduler.run()
    assert parent_done.get()
    assert not scheduler.is_running(parent)


def test_fork_result_delayed_await_completion_with_rescheduled_command(scheduler):
    child_run_count, parent_done = Ref(0), Ref(False)

    async def child_body(co: Coroutine):
        if child_run_count.increment_and_get() > 1:
            await co.park()

    child = Command.no_requirements(child_body).named("Child")

    async def parent_body(co: Coroutine):
        fork_result = co.fork(child)
        await co.yield_()
        await co.yield_()
        await fork_result.await_completion()
        parent_done.set(True)

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert child_run_count.get() == 1
    assert not scheduler.is_running(child)
    scheduler.schedule(child)
    scheduler.run()
    assert scheduler.is_running(child)
    assert child_run_count.get() == 2
    assert not parent_done.get()
    scheduler.run()
    assert parent_done.get()
    assert not scheduler.is_running(parent)
    assert scheduler.is_running(child)


def test_fork_result_delayed_await_completion_partial_reschedule(scheduler):
    c1_run_count, c2_done, parent_done = Ref(0), Ref(False), Ref(False)

    async def c1_body(co: Coroutine):
        if c1_run_count.increment_and_get() > 1:
            await co.park()

    async def c2_body(co: Coroutine):
        await co.wait_until(c2_done.get)

    c1 = Command.no_requirements(c1_body).named("C1")
    c2 = Command.no_requirements(c2_body).named("C2")

    async def parent_body(co: Coroutine):
        fork_result = co.fork(c1, c2)
        await co.yield_()
        await fork_result.await_completion()
        parent_done.set(True)

    parent = Command.no_requirements(parent_body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert c1_run_count.get() == 1
    assert not scheduler.is_running(c1)
    assert scheduler.is_running(c2)
    scheduler.schedule(c1)
    scheduler.run()
    assert c1_run_count.get() == 2
    assert scheduler.is_running(c1)
    assert scheduler.is_running(c2)
    assert not parent_done.get()
    c2_done.set(True)
    scheduler.run()
    assert parent_done.get()
    assert not scheduler.is_running(parent)
    assert scheduler.is_running(c1)


def test_fork_result_delayed_await_completion_large_set(scheduler):
    n = 80
    flags = [Ref(False) for _ in range(n)]
    run_counts = [0] * n
    parent_done = Ref(False)

    def make(idx):
        async def body(co: Coroutine):
            run_counts[idx] += 1
            if run_counts[idx] == 1:
                await co.wait_until(flags[idx].get)
            else:
                await co.park()

        return Command.no_requirements(body).named(f"LargeChild[{idx}]")

    children = [make(i) for i in range(n)]

    async def parent_body(co: Coroutine):
        fork_result = co.fork(children)
        await co.yield_()
        await fork_result.await_completion()
        parent_done.set(True)

    parent = Command.no_requirements(parent_body).named("ParentLarge")
    scheduler.schedule(parent)
    scheduler.run()
    for i in [*range(0, 40), *range(64, 72)]:
        flags[i].set(True)
    scheduler.run()
    for i in [*range(20, 40), *range(68, 72)]:
        scheduler.schedule(children[i])
    scheduler.run()
    assert not parent_done.get()
    assert scheduler.is_running(parent)
    for i in [*range(40, 64), *range(72, n)]:
        flags[i].set(True)
    scheduler.run()
    assert parent_done.get()
    assert not scheduler.is_running(parent)
    for i in [*range(20, 40), *range(68, 72)]:
        assert scheduler.is_running(children[i])
