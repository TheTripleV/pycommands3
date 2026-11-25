"""The asyncio runtime's extra capability: awaiting foreign asyncio awaitables."""

import asyncio
import threading
import time

import pytest

import commands3.aio.asyncawait as aio_async
import commands3.aio.generators as aio_gen
import commands3.aio.greenlets as aio_gl
import commands3.custom.asyncawait as custom_async
from commands3._common.api import Command


def run_until(s, pred, max_cycles=500, sleep=0.002):
    for _ in range(max_cycles):
        s.run()
        if pred():
            return True
        time.sleep(sleep)
    return False


def test_await_to_thread_inside_command():
    s = aio_async.Scheduler.create_independent_scheduler()
    out = {}

    def blocking_compute():
        time.sleep(0.02)
        return threading.get_ident()

    async def body(co):
        out["thread"] = await asyncio.to_thread(blocking_compute)
        await co.yield_()
        out["done"] = True

    c = Command.no_requirements(body).named("offload")
    s.schedule(c)
    s.run()
    assert s.is_running(c)
    assert run_until(s, lambda: "done" in out)
    assert out["thread"] != threading.get_ident()


def test_asyncio_sleep_resolves_at_cycle_granularity():
    s = aio_async.Scheduler.create_independent_scheduler()
    out = {}

    async def body(co):
        await asyncio.sleep(0.01)  # wall-clock based, progresses while run() executes
        out["done"] = True

    s.schedule(Command.no_requirements(body).named("sleep"))
    assert run_until(s, lambda: "done" in out)


def test_await_future_op_for_non_async_styles():
    for mod, src in (
        (aio_gen, "def body(co):\n    out['v'] = yield from co.await_future(work())\n"),
        (aio_gl, "def body(co):\n    out['v'] = co.await_future(work())\n"),
    ):
        s = mod.Scheduler.create_independent_scheduler()
        out = {}

        async def work():
            await asyncio.sleep(0)
            return 42

        ns = {"out": out, "work": work}
        exec(src, ns)
        s.schedule(Command.no_requirements(ns["body"]).named("c"))
        assert run_until(s, lambda: "v" in out)
        assert out["v"] == 42


def test_custom_runtime_rejects_foreign_awaitables():
    s = custom_async.Scheduler.create_independent_scheduler()
    foreign = asyncio.sleep(0)

    async def body(co):
        await co.await_future(foreign)

    s.schedule(Command.no_requirements(body).named("c"))
    try:
        with pytest.raises(TypeError, match="asyncio runtime"):
            s.run()
    finally:
        foreign.close()


def test_cancel_while_awaiting_foreign_future_cancels_it():
    s = aio_async.Scheduler.create_independent_scheduler()
    started = threading.Event()
    holder = {}

    async def body(co):
        fut = asyncio.get_running_loop().create_future()
        holder["fut"] = fut
        try:
            await fut
        finally:
            holder["finally"] = True

    c = Command.no_requirements(body).named("c")
    s.schedule(c)
    s.run()
    s.cancel(c)
    assert holder["finally"]
    assert holder["fut"].cancelled()


def test_each_command_run_is_a_named_asyncio_task():
    s = aio_async.Scheduler.create_independent_scheduler()
    names = []

    async def body(co):
        names.append(asyncio.current_task().get_name())
        await co.park()

    s.schedule(Command.no_requirements(body).named("Drive To Goal"))
    s.run()
    assert names == ["Drive To Goal"]


def test_run_from_inside_loop_is_rejected():
    s = aio_async.Scheduler.create_independent_scheduler()

    async def body(co):
        s.run()

    s.schedule(Command.no_requirements(body).named("c"))
    with pytest.raises(RuntimeError):
        s.run()
