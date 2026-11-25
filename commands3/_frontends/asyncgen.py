"""Async-generator front-end: a bare ``yield`` yields one cycle, ``await`` everything else.

    async def body(co: Coroutine):
        while not at_goal():
            drive()
            yield                       # one cycle
        await co.await_(other_command)

A ``yield``-ed value is recorded as the command's *published status* (handy for state
machines that want to expose their current state), retrievable via ``Coroutine.status``.
Bodies without any ``yield`` (plain ``async def``) work too.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .._common.coroutine import get_current_coroutine
from .._common.park import LIVE, Park
from .._common.scheduler import Frontend, Task
from .asyncawait import Coroutine as _AsyncCoroutine, GlobalCoroutine as _GlobalAsyncCoroutine


class Coroutine(_AsyncCoroutine):
    """Same API as the async/await front-end, plus the value last ``yield``-ed."""

    status: Any = None


class AsyncGenTask(Task):
    __slots__ = ("_body", "_co", "_agen", "_coro", "_pending")

    def __init__(self, body: Callable[[], Any], co: Coroutine):
        self._body = body
        self._co = co
        self._agen: Any = None
        self._coro: Any = None
        self._pending: Any = None  # in-flight asend() awaitable, kept across cycles

    def step(self) -> Any:
        if self._agen is None and self._coro is None:
            r = self._body()
            if inspect.isasyncgen(r):
                self._agen = r
            elif inspect.iscoroutine(r):
                self._coro = r
            elif inspect.isgenerator(r):
                raise TypeError("async generator front-end: bodies must be `async def`")
            else:
                raise StopIteration(r)
        if self._coro is not None:
            return self._coro.send(None)
        if self._pending is None:
            assert self._agen is not None
            self._pending = self._agen.asend(None)
        try:
            y = self._pending.send(None)
        except StopIteration as e:  # the body hit a bare `yield <value>`
            self._pending = None
            self._co.status = e.value
            return e.value if isinstance(e.value, Park) else LIVE
        except StopAsyncIteration:
            self._pending = None
            raise StopIteration(None) from None
        return y  # something it awaited suspended (a Park, or a foreign future)

    def close(self) -> None:
        if self._coro is not None:
            self._coro.close()
            return
        if self._agen is None:
            return
        suspended_in_cleanup = False
        if self._pending is not None:
            pending, self._pending = self._pending, None
            pending.close()  # throws GeneratorExit at the pending await; finally runs
            # CPython quirk: if the finally block hits a bare `yield`, asend.close() lets the
            # generator suspend there silently (the rest of the cleanup would never run).
            suspended_in_cleanup = self._agen.ag_frame is not None
        closer = self._agen.aclose()
        try:
            closer.send(None)
        except StopIteration:
            if not suspended_in_cleanup:
                return
        else:
            closer.close()
        raise RuntimeError("async generator command tried to suspend while being canceled")


class AsyncGenFrontend(Frontend):
    name = "asyncgen"
    coroutine_class = Coroutine

    def make_task(self, body, co) -> Task:
        return AsyncGenTask(body, co)  # type: ignore[arg-type]


class GlobalCoroutine(_GlobalAsyncCoroutine):
    """Global ``co`` proxy for async-generator commands without a ``co`` parameter."""

    @property
    def status(self) -> Any:
        return get_current_coroutine().status  # type: ignore[attr-defined]


co = GlobalCoroutine()


class AsyncGenNoCoFrontend(AsyncGenFrontend):
    name = "asyncgen_noco"
    pass_co = False
