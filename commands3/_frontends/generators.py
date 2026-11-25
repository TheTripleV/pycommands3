"""Generator front-end (the robotpy-commands-v2 PR #12 style).

    def body(co: Coroutine):
        while not at_goal():
            drive()
            yield                      # one cycle
        yield from co.await_(other_command)
"""

from __future__ import annotations

import inspect
import warnings
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable

from .._common.coroutine import CoroutineCore, ForkResult, WaitResult
from .._common.park import Op
from .._common.scheduler import Frontend, Task

if TYPE_CHECKING:
    from .._common.command import Command


class GenOp:
    """Iterable wrapper around a primitive op, consumed with ``yield from``.

    Python gives generators no "never awaited" warning, so this wrapper adds one for the
    framework's own operations (it cannot help for user-defined helper generators)."""

    __slots__ = ("_op", "_used", "__weakref__")

    def __init__(self, op: Op):
        self._op = op
        self._used = False

    def __iter__(self) -> Generator[Any, Any, Any]:
        self._used = True
        return self._op

    def __del__(self) -> None:
        if not self._used:
            warnings.warn(
                "commands3: a coroutine operation was created but never run "
                "(did you forget `yield from`?)",
                RuntimeWarning,
                stacklevel=2,
            )


class Coroutine(CoroutineCore):
    """Coroutine handle for generator command bodies: use ``yield from co.<op>(...)``,
    or a bare ``yield`` to yield for one cycle."""

    def _wrap(self, op: Op) -> GenOp:
        return GenOp(op)

    def yield_(self) -> GenOp:
        return GenOp(self.op_yield())

    def park(self) -> GenOp:
        return GenOp(self.op_park())

    def await_(self, command: "Command") -> GenOp:
        return GenOp(self.op_await(command))

    def await_all(self, *commands: "Command | Iterable[Command]") -> GenOp:
        return GenOp(self.op_await_all(*commands))

    def await_any(self, *commands: "Command | Iterable[Command]") -> GenOp:
        return GenOp(self.op_await_any(*commands))

    def wait(self, seconds: float) -> GenOp:
        return GenOp(self.op_wait(seconds))

    def wait_until(self, condition: Callable[[], bool], timeout: float | None = None) -> GenOp:
        return GenOp(self.op_wait_until(condition, timeout))

    def await_future(self, awaitable: Any) -> GenOp:
        return GenOp(self.op_await_future(awaitable))


class GeneratorTask(Task):
    __slots__ = ("_body", "_gen")

    def __init__(self, body: Callable[[], Any]):
        self._body = body
        self._gen: Any = None

    def step(self) -> Any:
        gen = self._gen
        if gen is None:
            r = self._body()
            if isinstance(r, GenOp):  # e.g. `mech.run(Coroutine.park)`
                r = iter(r)
            if inspect.isgenerator(r):
                gen = self._gen = r
            elif inspect.iscoroutine(r) or inspect.isasyncgen(r):
                if inspect.iscoroutine(r):
                    r.close()
                raise TypeError(
                    "This scheduler uses the generator front-end; command bodies must be "
                    "plain generator functions (got an async function)."
                )
            else:
                raise StopIteration(r)  # no `yield` in the body: a one-shot command
        return gen.send(None)  # type: ignore[arg-type]

    def close(self) -> None:
        if self._gen is not None:
            self._gen.close()


class GeneratorFrontend(Frontend):
    name = "generators"
    coroutine_class = Coroutine

    def make_task(self, body, co) -> Task:
        return GeneratorTask(body)
