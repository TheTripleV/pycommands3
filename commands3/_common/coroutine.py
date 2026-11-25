"""Port of Coroutine.java as front-end-neutral primitive ops.

``CoroutineCore`` implements every operation of the Java ``Coroutine`` class. Operations that
suspend are exposed as ``op_*`` generators that yield :class:`Park` objects; each front-end
wraps them in its own syntax (``yield from``, ``await``, a greenlet switch, ...). Operations
that never suspend (``fork``, ``scheduler``, ``request_cancellation``) are plain methods.

Two Java behaviours are modelled with internal ``BaseException`` subclasses instead of a
"yield forever" state, because Python can unwind a coroutine where Java can only abandon it:

* fork failure with cancel-on-fork-failure enabled raises :class:`ForkFailedInterrupt`; the
  scheduler then cancels the whole composition (Java: ``ForkFailed`` state + IRQ handling).
* ``request_cancellation()`` raises :class:`CancellationRequested` (Java: ``CANCELED`` state).

Both inherit from ``BaseException`` so ``except Exception`` in user code cannot swallow them,
and ``finally`` blocks run while they propagate.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, NoReturn

from .park import FROZEN, LIVE, Op, ParkedOnCondition, ParkedUntil, ParkedWhile
from .results import ConflictDetector, Failure, ScheduleResult, Successful
from .time import get_time_ns
from .tracker import CommandRunTracker

if TYPE_CHECKING:
    from .command import Command
    from .scheduler import SchedulerBase


class CancellationRequested(BaseException):
    """Internal: the running command asked to be canceled."""


class ForkFailedInterrupt(BaseException):
    """Internal: a fork failed and the coroutine is configured to cancel on fork failure."""

    def __init__(self, result: "ForkResult"):
        super().__init__(result)
        self.result = result


class Unwind(BaseException):
    """Internal: this command was canceled while on the call stack (e.g. an ancestor of a
    command that canceled it); unwind it now, running its ``finally`` blocks."""


class WaitResult(enum.Enum):
    CONDITION_MET = False
    TIMED_OUT = True

    def timed_out(self) -> bool:
        return self.value


def flatten_commands(args: tuple) -> list["Command"]:
    """Accept ``f(a, b)``, ``f([a, b])`` or a mix, like the Java varargs/Collection overloads."""
    from .command import Command

    out: list[Command] = []
    for a in args:
        if isinstance(a, Command):
            out.append(a)
        elif a is None:
            raise TypeError("command cannot be None")
        else:
            for c in a:
                if c is None:
                    raise TypeError("command cannot be None")
                out.append(c)
    return out


class ForkResult:
    """Result of fork/await operations (port of Coroutine.ForkResult)."""

    def __init__(self, co: "CoroutineCore", successes: Iterable[Successful],
                 failures: Iterable[Failure]):
        self._co = co
        self._forked = tuple(successes)
        self._failed = tuple(failures)

    def successful(self) -> bool:
        return not self._failed

    def failed(self) -> bool:
        return not self.successful()

    def partial_success(self) -> bool:
        return bool(self._forked) and bool(self._failed)

    def get_failed_commands(self) -> tuple[Failure, ...]:
        return self._failed

    def get_forked_commands(self) -> tuple[Successful, ...]:
        return self._forked

    def op_await_completion(self) -> Op:
        if not self._forked:
            return
        tracker = CommandRunTracker.of(self._co._scheduler, self._forked)
        while tracker.is_any_running():
            yield LIVE

    def await_completion(self) -> Any:
        """Front-end flavoured wrapper (awaitable / iterable / blocking)."""
        return self._co._wrap(self.op_await_completion())

    def __repr__(self) -> str:
        return f"ForkResult(forked={list(self._forked)}, failed={list(self._failed)})"


class CoroutineCore:
    """Shared implementation of the Java Coroutine API. Front-ends subclass it."""

    def __init__(self, scheduler: "SchedulerBase", cancel_on_fork_failure: bool = True):
        self._scheduler = scheduler
        self._cancel_on_fork_failure = cancel_on_fork_failure

    # ----------------------------------------------------------------- front-end hook --
    def _wrap(self, op: Op) -> Any:
        """Expose a primitive op in the front-end's syntax. Overridden by front-ends."""
        return op

    # ------------------------------------------------------------------- non-suspending --
    def _require_mounted(self) -> None:
        if self._scheduler._mounted is not self:
            raise RuntimeError("Coroutines can only be used by the command bound to them")

    def set_cancel_on_fork_failure(self, cancel_on_fork_failure: bool) -> None:
        self._cancel_on_fork_failure = cancel_on_fork_failure

    def is_cancel_on_fork_failure(self) -> bool:
        return self._cancel_on_fork_failure

    def scheduler(self) -> "SchedulerBase":
        self._require_mounted()
        return self._scheduler

    def request_cancellation(self) -> NoReturn:
        """Cancel the command bound to this coroutine (and its children), right now."""
        self._require_mounted()
        raise CancellationRequested()

    def _check_all_forkable(self, commands: list["Command"]) -> ForkResult:
        ConflictDetector.throw_if_conflicts(commands)
        successes: list[Successful] = []
        failures: list[Failure] = []
        for c in commands:
            r = self._scheduler.is_schedulable(c)
            (successes if r.successful() else failures).append(r)  # type: ignore[arg-type]
        result = ForkResult(self, successes, failures)
        if result.failed() and self._cancel_on_fork_failure:
            raise ForkFailedInterrupt(result)
        return result

    def _do_fork(self, commands: list["Command"]) -> ForkResult:
        successes: list[Successful] = []
        failures: list[Failure] = []
        for c in commands:
            r = self._scheduler.schedule(c)
            (successes if r.successful() else failures).append(r)  # type: ignore[arg-type]
        result = ForkResult(self, successes, failures)
        if failures and self._cancel_on_fork_failure:
            raise ForkFailedInterrupt(result)
        return result

    def fork(self, *commands: "Command | Iterable[Command]") -> ForkResult:
        """Schedule child commands and return immediately. Children start running right away
        (same scheduler cycle) and never outlive this command."""
        self._require_mounted()
        cmds = flatten_commands(commands)
        check = self._check_all_forkable(cmds)
        if check.failed():
            return check
        return self._do_fork(cmds)

    # ----------------------------------------------------------------------- suspending --
    def op_yield(self) -> Op:
        self._require_mounted()
        yield LIVE
        return True

    def op_park(self) -> Op:
        self._require_mounted()
        yield FROZEN
        raise AssertionError("unreachable: frozen coroutines are never remounted")

    def op_await(self, command: "Command") -> Op:
        self._require_mounted()
        if command is None:
            raise TypeError("command cannot be None")
        sched = self._scheduler
        r = sched.schedule(command)
        if isinstance(r, Successful):
            rid = r.run_id
            cond = ParkedWhile(lambda: sched.run_id(command) == rid)
            if not cond.condition_met():
                yield cond
            return ForkResult(self, (r,), ())
        result = ForkResult(self, (), (r,))  # type: ignore[arg-type]
        if self._cancel_on_fork_failure:
            raise ForkFailedInterrupt(result)
        return result

    def op_await_all(self, *commands: "Command | Iterable[Command]") -> Op:
        self._require_mounted()
        cmds = flatten_commands(commands)
        check = self._check_all_forkable(cmds)
        if check.failed():
            return check
        result = self._do_fork(cmds)
        if result.failed():
            return result
        tracker = CommandRunTracker.of(self._scheduler, result.get_forked_commands())
        cond = ParkedWhile(tracker.is_any_running)
        if not cond.condition_met():
            yield cond
        return result

    def op_await_any(self, *commands: "Command | Iterable[Command]") -> Op:
        self._require_mounted()
        cmds = flatten_commands(commands)
        check = self._check_all_forkable(cmds)
        if check.failed():
            return check
        result = self._do_fork(cmds)
        if result.failed():
            return result
        sched = self._scheduler
        tracker = CommandRunTracker.of(sched, result.get_forked_commands())
        cond = ParkedWhile(tracker.are_all_running)
        if not cond.condition_met():
            yield cond
        for fork in result.get_forked_commands():
            if sched.run_id(fork.command) == fork.run_id:
                sched.cancel(fork.command)
        return result

    def op_wait(self, seconds: float) -> Op:
        self._require_mounted()
        if seconds is None:
            raise TypeError("duration cannot be None")
        if seconds > 0:
            yield ParkedUntil(get_time_ns() + int(seconds * 1e9))

    def op_wait_until(self, condition: Callable[[], bool],
                      timeout: float | None = None) -> Op:
        self._require_mounted()
        if condition is None:
            raise TypeError("condition cannot be None")
        if timeout is None:
            cond = ParkedOnCondition(condition)
            if not cond.condition_met():
                yield cond
            return WaitResult.CONDITION_MET
        if condition():
            return WaitResult.CONDITION_MET
        target = get_time_ns() + int(timeout * 1e9)
        yield ParkedOnCondition(lambda: condition() or get_time_ns() >= target)
        # Tie: the condition wins, like Java.
        return WaitResult.CONDITION_MET if condition() else WaitResult.TIMED_OUT

    def op_await_future(self, awaitable: Any) -> Op:
        """Wait on a foreign (asyncio) awaitable. Only supported by the asyncio runtime."""
        self._require_mounted()
        fut, park = self._scheduler._park_on_foreign(awaitable)
        if not fut.done():
            yield park
        return fut.result()
