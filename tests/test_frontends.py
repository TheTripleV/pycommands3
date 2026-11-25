"""Behaviour that intentionally differs between coroutine styles (the comparison's subject)."""

import gc
import importlib
import warnings

import pytest

from commands3._common.api import Command

RUNTIMES = ["custom", "aio"]


def sched_for(runtime, style):
    return importlib.import_module(f"commands3.{runtime}.{style}").Scheduler.create_independent_scheduler()


# ---------------------------------------------------------------- generators -----------
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_generators_forgotten_yield_from_on_user_helper_is_silent(runtime):
    s = sched_for(runtime, "generators")
    ticks = []

    def helper(co):
        for _ in range(3):
            ticks.append(1)
            yield

    def body(co):
        helper(co)  # BUG: forgot `yield from` -> nothing happens, no warning possible
        yield

    gc.collect()  # flush garbage from earlier tests so only this test's warnings are seen
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s.schedule(Command.no_requirements(body).named("c"))
        for _ in range(5):
            s.run()
        gc.collect()
    assert ticks == []
    assert not [x for x in w if issubclass(x.category, RuntimeWarning)]


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_generators_forgotten_yield_from_on_framework_op_warns(runtime):
    s = sched_for(runtime, "generators")

    def body(co):
        co.wait(1.0)  # BUG: forgot `yield from`; GenOp.__del__ warns
        yield

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s.schedule(Command.no_requirements(body).named("c"))
        s.run()
        gc.collect()
    assert any("forget `yield from`" in str(x.message) for x in w)


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_generators_body_without_yield_is_a_one_shot(runtime):
    s = sched_for(runtime, "generators")
    ran = []
    c = Command.no_requirements(lambda co: ran.append(1)).named("c")
    s.schedule(c)
    s.run()
    assert ran == [1] and not s.is_running(c)


# ------------------------------------------------------------------- async ------------
@pytest.mark.parametrize("style", ["asyncawait", "asyncgen"])
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_async_forgotten_await_warns_even_for_user_helpers(runtime, style):
    s = sched_for(runtime, style)

    async def helper(co):
        await co.yield_()

    async def body(co):
        helper(co)  # BUG: forgot await -> "coroutine 'helper' was never awaited"
        await co.yield_()

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s.schedule(Command.no_requirements(body).named("c"))
        s.run()
        gc.collect()
    assert any("was never awaited" in str(x.message) for x in w)


@pytest.mark.parametrize("style", ["asyncawait", "generators", "asyncgen"])
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_cleanup_cannot_suspend(runtime, style):
    """Stackless styles: a finally block that suspends during cancellation is an error."""
    s = sched_for(runtime, style)
    src = {
        "asyncawait": "async def body(co):\n try:\n  await co.park()\n finally:\n  await co.yield_()\n",
        "asyncgen": "async def body(co):\n try:\n  await co.park()\n finally:\n  yield\n",
        "generators": "def body(co):\n try:\n  yield from co.park()\n finally:\n  yield\n",
    }[style]
    ns = {}
    exec(src, ns)
    c = Command.no_requirements(ns["body"]).named("c")
    s.schedule(c)
    s.run()
    with pytest.raises(RuntimeError):
        s.cancel(c)


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_wrong_style_body_is_rejected_with_a_clear_error(runtime):
    s = sched_for(runtime, "asyncawait")

    def gen_body(co):
        yield

    s.schedule(Command.no_requirements(gen_body).named("c"))
    with pytest.raises(TypeError, match="async/await front-end"):
        s.run()


# --------------------------------------------------------------- async generators ------
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_asyncgen_yielded_value_is_published_status(runtime):
    s = sched_for(runtime, "asyncgen")
    seen = []

    async def body(co):
        yield "HOMING"
        yield "MOVING"
        await co.park()

    c = Command.no_requirements(body).named("c")
    s.schedule(c)
    s.run()
    seen.append(s._running[c].coroutine.status)
    s.run()
    seen.append(s._running[c].coroutine.status)
    assert seen == ["HOMING", "MOVING"]


# -------------------------------------------------------------------- greenlets ---------
@pytest.mark.parametrize("runtime", RUNTIMES)
def test_greenlet_suspends_from_any_call_depth(runtime):
    s = sched_for(runtime, "greenlets")
    ticks = []

    def deep(co, n):
        if n == 0:
            co.yield_()
            ticks.append(1)
            return
        deep(co, n - 1)

    def body(co):
        for _ in range(3):
            deep(co, 50)

    s.schedule(Command.no_requirements(body).named("c"))
    for _ in range(5):
        s.run()
    assert len(ticks) == 3


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_greenlet_can_suspend_inside_a_callback(runtime):
    """Power and hazard: nothing at the call site shows sorted() may suspend the command."""
    s = sched_for(runtime, "greenlets")
    seen = []

    def body(co):
        def key(x):
            co.yield_()
            seen.append(x)
            return x

        sorted([3, 1, 2], key=key)

    s.schedule(Command.no_requirements(body).named("c"))
    s.run()
    assert seen == []
    s.run()
    assert seen == [3]


@pytest.mark.parametrize("runtime", RUNTIMES)
def test_greenlet_cleanup_cannot_suspend(runtime):
    s = sched_for(runtime, "greenlets")

    def body(co):
        try:
            co.park()
        finally:
            co.yield_()

    c = Command.no_requirements(body).named("c")
    s.schedule(c)
    s.run()
    with pytest.raises(RuntimeError):
        s.cancel(c)


@pytest.mark.parametrize("runtime", RUNTIMES)
@pytest.mark.parametrize("kind", ["async", "generator"])
def test_greenlet_rejects_wrong_style_body(runtime, kind):
    s = sched_for(runtime, "greenlets")
    ran = []

    if kind == "async":
        async def body(co):
            ran.append(1)
    else:
        def body(co):
            ran.append(1)
            yield

    s.schedule(Command.no_requirements(body).named("c"))
    with pytest.raises(TypeError, match="greenlet front-end"):
        s.run()
    assert ran == []
