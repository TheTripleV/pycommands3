"""greenlet front-end: stackful coroutines, the direct analog of Java's continuations.

    def body(co: Coroutine):
        while not at_goal():
            drive()
            co.yield_()               # can be called from any depth of plain function calls
        co.await_(other_command)
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable, Iterable, NoReturn

from .._common.coroutine import (
    CoroutineCore,
    ForkResult,
    WaitResult,
    _GlobalCoroutineBase,
    get_current_coroutine,
)
from .._common.park import Op
from .._common.scheduler import Frontend, Task

try:
    import greenlet as _gl
except ImportError:  # pragma: no cover
    _gl: Any = None

if TYPE_CHECKING:
    from .._common.command import Command

_DONE = object()


def _drive(op: Op) -> Any:
    """Run a primitive op inside the command's greenlet: each Park it yields is handed to
    whoever resumed this greenlet (the scheduler, or a parent that forked us)."""
    try:
        req = next(op)
        while True:
            _gl.getcurrent().parent.switch(req)
            req = op.send(None)
    except StopIteration as e:
        return e.value


class Coroutine(CoroutineCore):
    """Blocking-style coroutine handle: no ``yield``/``await`` keywords anywhere."""

    def _wrap(self, op: Op) -> Any:
        return _drive(op)

    def yield_(self) -> bool:
        return _drive(self.op_yield())

    def park(self) -> NoReturn:
        _drive(self.op_park())
        raise AssertionError("unreachable")

    def await_(self, command: "Command") -> ForkResult:
        return _drive(self.op_await(command))

    def await_all(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return _drive(self.op_await_all(*commands))

    def await_any(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return _drive(self.op_await_any(*commands))

    def wait(self, seconds: float) -> None:
        _drive(self.op_wait(seconds))

    def wait_until(self, condition: Callable[[], bool], timeout: float | None = None) -> WaitResult:
        return _drive(self.op_wait_until(condition, timeout))

    def await_future(self, awaitable: Any) -> Any:
        return _drive(self.op_await_future(awaitable))


class GreenletTask(Task):
    __slots__ = ("_g",)

    def __init__(self, body: Callable[[], Any]):
        if _gl is None:
            raise ImportError("the greenlet front-end requires `pip install greenlet`")

        def entry():
            r = body()
            if inspect.iscoroutine(r) or inspect.isgenerator(r) or inspect.isasyncgen(r):
                if inspect.iscoroutine(r) or inspect.isgenerator(r):
                    r.close()
                raise TypeError(
                    "This scheduler uses the greenlet front-end; command bodies must be plain "
                    "functions that call co.yield_() etc. (got a generator or async function)."
                )
            return _DONE

        self._g = _gl.greenlet(entry)

    def step(self) -> Any:
        g = self._g
        g.parent = _gl.getcurrent()  # yields return to whoever resumed us
        r = g.switch()
        if g.dead:
            raise StopIteration(None)
        return r

    def close(self) -> None:
        g = self._g
        if g.dead or not g:  # finished, or never started
            return
        g.parent = _gl.getcurrent()
        r = g.throw(_gl.GreenletExit)  # unwinds the green stack, running finally blocks
        if not g.dead:
            raise RuntimeError(f"greenlet command suspended while being canceled ({r!r})")


class GreenletFrontend(Frontend):
    name = "greenlets"
    coroutine_class = Coroutine

    def make_task(self, body, co) -> Task:
        return GreenletTask(body)


def _cur() -> Coroutine:
    return get_current_coroutine()  # type: ignore[return-value]


class GlobalCoroutine(_GlobalCoroutineBase):
    """Global ``co`` proxy for greenlet commands without a ``co`` parameter."""

    def yield_(self) -> bool:
        return _cur().yield_()

    def park(self) -> NoReturn:
        _cur().park()

    def await_(self, command: "Command") -> ForkResult:
        return _cur().await_(command)

    def await_all(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return _cur().await_all(*commands)

    def await_any(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        return _cur().await_any(*commands)

    def wait(self, seconds: float) -> None:
        _cur().wait(seconds)

    def wait_until(self, condition: Callable[[], bool], timeout: float | None = None) -> WaitResult:
        return _cur().wait_until(condition, timeout)

    def await_future(self, awaitable: Any) -> Any:
        return _cur().await_future(awaitable)


co = GlobalCoroutine()


class GreenletNoCoFrontend(GreenletFrontend):
    name = "greenlets_noco"
    pass_co = False
