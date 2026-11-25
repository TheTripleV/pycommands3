"""asyncio runtime: every command run is carried by a real ``asyncio.Task``.

How it keeps Commands v3 semantics on top of asyncio:

* ``Scheduler.run()`` is synchronous (robot loop) and drives a private event loop with
  ``run_until_complete`` for exactly one cycle.
* Each command run gets a *driver* task. The driver steps the user's body to its next
  suspension, reports the outcome, then awaits a per-run "wake" future. The scheduler wakes
  drivers one at a time, in Java's order (children before parents), awaiting each report;
  so ordering is deterministic and identical to the custom runtime.
* Forked children are created with ``eager_start=True`` so they run synchronously inside
  ``fork()``: no dead cycles at any nesting depth.
* User code may await *foreign* asyncio awaitables (``asyncio.sleep``, ``asyncio.to_thread``,
  network clients...). The command parks until the future is done and is then resumed at its
  normal turn. Such futures progress only while the loop runs (i.e. inside ``run()``), so
  they resolve with scheduler-cycle granularity.
* Cancellation closes the user's body synchronously (``finally`` blocks run inside
  ``cancel()``, exactly like the custom runtime) and then cancels the driver task.
"""

from __future__ import annotations

import asyncio
import selectors
import weakref
from typing import Any

from .._common.park import ParkedOnFuture, Park
from .._common.scheduler import CommandState, Kind, Outcome, SchedulerBase, step_task
from .._common.time import get_time_ns


class _TickSelector(selectors.DefaultSelector):  # type: ignore[misc,valid-type]
    """Selector that performs one real (non-blocking) poll per scheduler cycle.

    asyncio polls the OS selector on *every* loop iteration, and each command resume costs
    two iterations, so the syscall dominated the runtime's cost. Skipping the extra
    zero-timeout polls keeps I/O and thread wakeups working (they are picked up at the next
    cycle's poll; call_soon_threadsafe callbacks land in the ready queue immediately) while
    removing ~2 syscalls per command per cycle. Blocking polls are never skipped.
    """

    polled_this_cycle = False

    def select(self, timeout=None):
        if timeout is not None and timeout <= 0 and self.polled_this_cycle:
            return []
        self.polled_this_cycle = True
        return super().select(timeout)


def _close_loop(loop: asyncio.AbstractEventLoop) -> None:
    if loop.is_closed() or loop.is_running():
        return
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()


class _AioData:
    __slots__ = ("driver", "wake", "reported", "outcome")

    def __init__(self) -> None:
        self.driver: asyncio.Task | None = None
        self.wake: asyncio.Future | None = None
        self.reported: asyncio.Future | None = None
        self.outcome: Outcome | None = None


class AsyncioScheduler(SchedulerBase):
    runtime_name = "asyncio"

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__()
        self._selector: _TickSelector | None = None
        if loop is None:
            self._selector = _TickSelector()
            loop = asyncio.SelectorEventLoop(self._selector)
        self._loop = loop
        self._owns_loop = self._selector is not None
        if self._owns_loop:
            weakref.finalize(self, _close_loop, loop)

    def close(self) -> None:
        """Cancel everything and close the private event loop."""
        self.cancel_all()
        if self._owns_loop:
            _close_loop(self._loop)

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    # ------------------------------------------------------------------ run ------------
    def run(self) -> None:
        if self._loop.is_running():
            raise RuntimeError(
                "Scheduler.run() drives its own asyncio loop and cannot be called from code "
                "running inside that loop (e.g. from a command)."
            )
        if self._selector is not None:
            self._selector.polled_this_cycle = False
        self._loop.run_until_complete(self._run_async())

    async def _run_async(self) -> None:
        start = get_time_ns()
        self._cancel_stale_bindings()
        self._unbind_stale_triggers()
        self._cancel_commands_that_cannot_run_in_disabled()
        await self._arun_periodic_sideloads()
        self._event_loop.poll()
        self._schedule_default_commands()
        self._promote_scheduled_commands()
        for st in reversed(list(self._running.values())):
            await self._arun_command(st)
        self._last_run_time_ms = (get_time_ns() - start) / 1e6

    async def _arun_command(self, state: CommandState) -> None:
        if not self._can_mount(state):
            return
        token = self._pre_mount(state)
        outcome = await self._amount_step(state)
        self._post_mount(state, token, outcome)

    async def _arun_periodic_sideloads(self) -> None:
        for st in list(self._sideloads):
            if not st.scope.active():
                self._sideloads.remove(st)
                self._close_task(st)
                continue
            if not st.park.should_mount():
                continue
            prev = self._mounted
            self._mounted = st.coroutine
            try:
                outcome = await self._amount_step(st)
            finally:
                self._mounted = prev
            self._finish_sideload(st, outcome)

    # -------------------------------------------------------------- driver -------------
    def _ensure_task(self, state) -> None:
        if state.task is None:
            co = state.coroutine
            cmd = state.command
            state.task = self._make_task(state, lambda: cmd.run(co), cmd._native)

    async def _driver(self, state) -> None:
        data: _AioData = state.aio
        while True:
            outcome = step_task(state.task)
            data.outcome = outcome
            rep = data.reported
            if rep is not None and not rep.done():
                rep.set_result(None)
            if type(outcome) is Outcome:  # terminal (done/cancel/error/...)
                return
            data.wake = self._loop.create_future()
            try:
                await data.wake
            except asyncio.CancelledError:
                return

    def _start_driver(self, state) -> Outcome:
        """Create the driver task eagerly: its first step runs synchronously, right here."""
        self._ensure_task(state)
        data = state.aio = _AioData()
        name = state.command.name() if state.command is not None else "sideload"
        data.driver = asyncio.Task(  # eager_start is missing from typeshed
            self._driver(state), loop=self._loop, name=name,
            eager_start=True)  # pyright: ignore[reportCallIssue]
        # Drivers park on their wake future between cycles; a scheduler that is simply
        # dropped (tests, robot shutdown) legitimately abandons them.
        data.driver._log_destroy_pending = False  # type: ignore[attr-defined]
        if data.outcome is None:  # pragma: no cover - only if the loop isn't running
            raise RuntimeError("asyncio runtime: command did not start eagerly")
        return data.outcome

    def _mount_step(self, state) -> Outcome:
        # Synchronous path: only used for the first mount of a forked child, which happens
        # while the parent's driver is executing inside the running loop.
        if state.aio is not None:
            raise RuntimeError("asyncio runtime: synchronous remount is not possible")
        return self._start_driver(state)

    async def _amount_step(self, state) -> Outcome:
        data: _AioData | None = state.aio
        if data is None:
            return self._start_driver(state)
        data.outcome = None
        data.reported = self._loop.create_future()
        assert data.wake is not None
        data.wake.set_result(None)
        await data.reported
        assert data.outcome is not None
        return data.outcome

    def _close_task(self, state) -> None:
        try:
            if not state.finished and state.task is not None:
                park = state.park
                try:
                    super()._close_task(state)
                finally:
                    if isinstance(park, ParkedOnFuture) and not park.future.done():
                        park.future.cancel()
        finally:
            self._stop_driver(state)

    def _stop_driver(self, state) -> None:
        data: _AioData | None = state.aio
        if data is not None and data.driver is not None and not data.driver.done():
            data.driver.cancel()
            if not self._loop.is_running() and not self._loop.is_closed():
                # Let the driver observe its cancellation now rather than at the next run().
                self._loop.call_soon(self._loop.stop)
                self._loop.run_forever()

    # ------------------------------------------------------------- foreign awaitables --
    def _foreign_yield(self, value: Any) -> Park:
        if asyncio.isfuture(value):
            return ParkedOnFuture(value)
        return super()._foreign_yield(value)

    def _park_on_foreign(self, awaitable: Any):
        fut = asyncio.ensure_future(awaitable, loop=self._loop)
        return fut, ParkedOnFuture(fut)
