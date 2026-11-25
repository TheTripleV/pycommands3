"""async/await front-end.

    async def body(co: Coroutine):
        while not at_goal():
            drive()
            await co.yield_()
        await co.await_(other_command)
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, NoReturn

from .._common.coroutine import (
    CoroutineCore,
    ForkResult,
    WaitResult,
    _GlobalCoroutineBase,
    get_current_coroutine,
)
from .._common.park import Op
from .._common.scheduler import Frontend, Task

if TYPE_CHECKING:
    from .._common.command import Command


class _AwaitOp:
    """Awaitable that delegates to a primitive op: ``await`` drives the op's generator, so
    the Park objects it yields travel up to whichever runtime is stepping the coroutine."""

    __slots__ = ("_op",)

    def __init__(self, op: Op):
        self._op = op

    def __await__(self):
        return self._op


async def _run(op: Op) -> Any:
    return await _AwaitOp(op)


class Coroutine(CoroutineCore):
    """The coroutine handle given to async command bodies.

    Every suspending method is a real ``async def``, so forgetting ``await`` is reported by
    type checkers (pyright ``reportUnusedCoroutine``) and by CPython at runtime.
    """

    def _wrap(self, op: Op) -> Awaitable[Any]:
        return _run(op)

    async def yield_(self) -> bool:
        """Yield control to the scheduler for one cycle."""
        return await _AwaitOp(self.op_yield())

    async def park(self) -> NoReturn:
        """Suspend forever; the command only ends when canceled or interrupted."""
        await _AwaitOp(self.op_park())
        raise AssertionError("unreachable")

    async def await_(self, command: "Command") -> ForkResult:
        """Fork ``command`` and wait for it to finish."""
        return await _AwaitOp(self.op_await(command))

    async def await_all(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return await _AwaitOp(self.op_await_all(*commands))

    async def await_any(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return await _AwaitOp(self.op_await_any(*commands))

    async def wait(self, seconds: float) -> None:
        await _AwaitOp(self.op_wait(seconds))

    async def wait_until(self, condition: Callable[[], bool],
                         timeout: float | None = None) -> WaitResult:
        return await _AwaitOp(self.op_wait_until(condition, timeout))

    async def await_future(self, awaitable: Any) -> Any:
        """Await a foreign asyncio awaitable (asyncio runtime). On the asyncio runtime you
        can also simply ``await`` asyncio objects directly."""
        return await _AwaitOp(self.op_await_future(awaitable))


class CoroutineTask(Task):
    __slots__ = ("_body", "_coro")

    def __init__(self, body: Callable[[], Any]):
        self._body = body
        self._coro: Any = None

    def step(self) -> Any:
        coro = self._coro
        if coro is None:
            r = self._body()
            if inspect.iscoroutine(r):
                coro = self._coro = r
            elif inspect.isgenerator(r) or inspect.isasyncgen(r):
                raise TypeError(
                    "This scheduler uses the async/await front-end; command bodies must be "
                    f"`async def` functions without bare `yield` (got {type(r).__name__})."
                )
            else:
                raise StopIteration(r)  # plain function: a one-shot command
        return coro.send(None)

    def close(self) -> None:
        if self._coro is not None:
            self._coro.close()


class AsyncAwaitFrontend(Frontend):
    name = "asyncawait"
    coroutine_class = Coroutine

    def make_task(self, body, co) -> Task:
        return CoroutineTask(body)


def _cur() -> Coroutine:
    return get_current_coroutine()  # type: ignore[return-value]


class GlobalCoroutine(_GlobalCoroutineBase):
    """Global ``co`` proxy for async/await commands without a ``co`` parameter."""

    async def yield_(self) -> bool:
        return await _cur().yield_()

    async def park(self) -> NoReturn:
        await _cur().park()
        raise AssertionError("unreachable")

    async def await_(self, command: "Command") -> ForkResult:
        return await _cur().await_(command)

    async def await_all(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return await _cur().await_all(*commands)

    async def await_any(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return await _cur().await_any(*commands)

    async def wait(self, seconds: float) -> None:
        await _cur().wait(seconds)

    async def wait_until(self, condition: Callable[[], bool],
                         timeout: float | None = None) -> WaitResult:
        return await _cur().wait_until(condition, timeout)

    async def await_future(self, awaitable: Any) -> Any:
        return await _cur().await_future(awaitable)


co = GlobalCoroutine()


class AsyncAwaitNoCoFrontend(AsyncAwaitFrontend):
    name = "asyncawait_noco"
    pass_co = False
