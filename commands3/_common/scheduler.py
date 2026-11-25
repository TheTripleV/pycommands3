"""Port of Scheduler.java, independent of coroutine style and runtime.

Subclasses provide:
  * ``frontend``: turns a user command body into a steppable task (generators, async/await,
    async generators, greenlet), see ``commands3._frontends``.
  * the runtime hooks ``_mount_step`` / ``_close_task`` / ``_park_on_foreign`` (custom or
    asyncio), see ``commands3._runtime``.

The scheduling algorithm (queueing, priorities, ancestry, eviction, default commands,
binding scopes, sideloads, events) lives here once.
"""

from __future__ import annotations

import enum
import itertools
import traceback
from typing import TYPE_CHECKING, Any, Callable, Iterable

from . import robot_state
from .binding import Binding, BindingScope, BindingType, ForCommand, capture_site
from .coroutine import (
    CancellationRequested,
    CoroutineCore,
    ForkFailedInterrupt,
    ForkResult,
    Unwind,
    set_current_coroutine,
)
from .park import LIVE, Park
from .registry import get_default_scheduler, register_variant, set_default_scheduler
from .results import (
    AlreadyRunning,
    Canceled,
    Completed,
    CompletedWithError,
    ForkFailure,
    Interrupted,
    LowerPriorityThanQueuedCommand,
    LowerPriorityThanRunningCommand,
    Mounted,
    RequiresUnsafeMechanisms,
    Scheduled,
    ScheduleResult,
    SchedulerEvent,
    Success,
    Yielded,
)
from .time import get_time_ns
from .trigger import EventLoop

from time import perf_counter_ns as _perf_ns

if TYPE_CHECKING:
    from .command import Command
    from .mechanism import Mechanism
    from .trigger import Trigger


# ======================================================================================
# Outcomes of running a command to its next suspension point
# ======================================================================================


class Kind(enum.Enum):
    PARKED = 0
    DONE = 1
    CANCEL_REQUESTED = 2
    FORK_FAILED = 3
    UNWOUND = 4
    ERROR = 5


class Outcome:
    __slots__ = ("kind", "value")

    def __init__(self, kind: Kind, value: Any = None):
        self.kind = kind
        self.value = value


OUT_DONE = Outcome(Kind.DONE)
OUT_CANCEL = Outcome(Kind.CANCEL_REQUESTED)
OUT_UNWOUND = Outcome(Kind.UNWOUND)


class Task:
    """Front-end adapter interface. ``step()`` runs the body to its next suspension and
    returns what it yielded (a Park, ``None`` for a bare yield, or a foreign awaitable);
    it raises StopIteration when the body returns. ``close()`` cancels it (finally runs)."""

    def step(self) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class Frontend:
    name = "?"
    coroutine_class: type[CoroutineCore] = CoroutineCore
    pass_co: bool = True

    def make_task(self, body: Callable[[], Any], co: CoroutineCore) -> Task:
        raise NotImplementedError


class NativeTask(Task):
    """Drives framework-internal Park-yielding generators (groups, state machines...)."""

    __slots__ = ("_factory", "_gen")

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._gen = None

    def step(self) -> Any:
        if self._gen is None:
            g = self._factory()
            if g is None or not hasattr(g, "send"):
                raise StopIteration(g)
            self._gen = g
        return self._gen.send(None)

    def close(self) -> None:
        if self._gen is not None:
            self._gen.close()


def step_task(task: Task) -> "Outcome | Any":
    """Run a task to its next suspension point and classify what happened."""
    try:
        y = task.step()
    except StopIteration:
        return OUT_DONE
    except CancellationRequested:
        return OUT_CANCEL
    except ForkFailedInterrupt as e:
        return Outcome(Kind.FORK_FAILED, e.result)
    except Unwind:
        return OUT_UNWOUND
    except Exception as e:
        return Outcome(Kind.ERROR, e)
    # Hot path: a suspension returns the yielded value itself (a Park, or a foreign
    # awaitable on the asyncio runtime); only terminal results allocate an Outcome.
    return LIVE if y is None else y


# ======================================================================================
# Per-run state
# ======================================================================================

_ids = itertools.count(1)


class CommandState:
    __slots__ = ("command", "parent", "coroutine", "binding", "id", "task", "park",
                 "last_runtime_ms", "total_runtime_ms", "on_stack", "doomed", "finished",
                 "aio")

    def __init__(self, command: "Command", parent: "Command | None", coroutine: CoroutineCore,
                 binding: Binding):
        self.command = command
        self.parent = parent
        self.coroutine = coroutine
        self.binding = binding
        self.id = next(_ids)
        self.task: Task | None = None
        self.park: Park = LIVE
        self.last_runtime_ms = -1.0
        self.total_runtime_ms = 0.0
        self.on_stack = False   # currently executing (in the ancestry stack)
        self.doomed = False     # canceled while on the stack; unwinds when control returns
        self.finished = False   # task has terminated; nothing to close
        self.aio: Any = None    # runtime-private data (asyncio runtime)

    def set_last_runtime_ms(self, ms: float) -> None:
        self.last_runtime_ms = ms
        self.total_runtime_ms += ms

    def __repr__(self) -> str:
        return (f"CommandState[command={self.command!r}, parent={self.parent!r}, "
                f"id={self.id}]")


class _SideloadState:
    __slots__ = ("scope", "coroutine", "task", "park", "finished", "on_stack", "doomed",
                 "aio", "command")

    def __init__(self, scope: BindingScope, coroutine: CoroutineCore):
        self.scope = scope
        self.coroutine = coroutine
        self.task: Task | None = None
        self.park: Park = LIVE
        self.finished = False
        self.on_stack = False
        self.doomed = False
        self.aio: Any = None
        self.command = None


# ======================================================================================
# Scheduler
# ======================================================================================


class SchedulerBase:
    frontend: Frontend
    runtime_name = "?"

    # ---------------------------------------------------------- construction ---------
    def __init__(self) -> None:
        self._default_command_bindings: dict["Mechanism", list[Binding]] = {}
        # The binding of each live (queued or running) run that is not command-scoped.
        # Entries are dropped when the run ends, so a stale scope only ever cancels the
        # run it started, never a later run of the same command.
        self._active_bindings: dict["Command", Binding] = {}
        self._bound_triggers: set["Trigger"] = set()
        self._queued: dict["Command", CommandState] = {}
        self._running: dict["Command", CommandState] = {}
        self._ancestry: list[CommandState] = []
        self._sideloads: list[_SideloadState] = []
        self._event_loop = EventLoop()
        self._last_run_time_ms = -1.0
        self._listeners: list[Callable[[SchedulerEvent], None]] = []
        self._mounted: CoroutineCore | None = None

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if getattr(cls, "_variant", False):
            register_variant(cls)

    @classmethod
    def _create(cls) -> "SchedulerBase":
        return cls()

    @classmethod
    def get_default(cls) -> "SchedulerBase":
        s = get_default_scheduler()
        if not isinstance(s, cls):
            raise RuntimeError(
                f"The default scheduler is a {type(s).__name__}, not a {cls.__name__}. "
                "Only one commands3 variant can provide the default scheduler per process."
            )
        return s

    @classmethod
    def create_independent_scheduler(cls) -> "SchedulerBase":
        return cls._create()

    @classmethod
    def set_default(cls, scheduler: "SchedulerBase | None") -> None:
        set_default_scheduler(scheduler)

    def close(self) -> None:
        """Release runtime resources (the asyncio runtime's event loop). Cancels all commands."""
        self.cancel_all()

    # ----------------------------------------------------------- runtime hooks -------
    def _mount_step(self, state: "CommandState | _SideloadState") -> Outcome:
        raise NotImplementedError

    def _close_task(self, state: "CommandState | _SideloadState") -> None:
        if state.finished or state.task is None:
            return
        state.finished = True
        state.task.close()

    def _park_on_foreign(self, awaitable: Any) -> "tuple[Any, Park]":
        raise TypeError(
            f"The {self.runtime_name} runtime cannot await foreign awaitables "
            f"({awaitable!r}); use the asyncio runtime (commands3.aio.*)"
        )

    def _set_mounted(self, co: CoroutineCore | None) -> None:
        self._mounted = co
        set_current_coroutine(co)

    def _make_task(self, state: "CommandState | _SideloadState", body: Callable[[], Any],
                   native: bool) -> Task:
        if native:
            return NativeTask(body)
        return self.frontend.make_task(body, state.coroutine)

    def _new_coroutine(self, cancel_on_fork_failure: bool = True) -> CoroutineCore:
        return self.frontend.coroutine_class(self, cancel_on_fork_failure)

    # -------------------------------------------------------- default commands -------
    def set_default_command(self, mechanism: "Mechanism", default_command: "Command") -> None:
        if not default_command.requires(mechanism):
            raise ValueError("A mechanism's default command must require that mechanism")
        if len(default_command.requirements()) > 1:
            raise ValueError("A mechanism's default command cannot require other mechanisms")
        current = self.current_command()
        scope = BindingScope.create_narrowest_scope(self)
        binding = Binding(scope, BindingType.CONTINUOUSLY_SCHEDULE_WHILE_HIGH, default_command,
                          capture_site(2))
        current_default = self.get_default_command_for(mechanism)
        self._default_command_bindings.setdefault(mechanism, []).append(binding)
        if current is not None and current is not current_default:
            self._process_default_commands(mechanism)

    def get_default_command_for(self, mechanism: "Mechanism") -> "Command | None":
        bindings = self._default_command_bindings.get(mechanism)
        if not bindings:
            return None
        return bindings[-1].command

    def _get_default_command_bindings_for(self, mechanism: "Mechanism") -> list[Binding]:
        return self._default_command_bindings.get(mechanism, [])

    # --------------------------------------------------------------- sideloads -------
    def sideload(self, callback: Callable[..., Any]) -> None:
        """Run ``callback`` as a periodic coroutine before triggers are polled."""
        co = self._new_coroutine(cancel_on_fork_failure=False)
        st = _SideloadState(BindingScope.create_narrowest_scope(self), co)
        body = (lambda: callback(co)) if self.frontend.pass_co else (lambda: callback())
        st.task = self._make_task(st, body, native=False)
        self._sideloads.append(st)

    def _sideload_native(self, op_factory: Callable[[CoroutineCore], Any]) -> None:
        co = self._new_coroutine(cancel_on_fork_failure=False)
        st = _SideloadState(BindingScope.create_narrowest_scope(self), co)
        st.task = self._make_task(st, lambda: op_factory(co), native=True)
        self._sideloads.append(st)

    def add_periodic(self, callback: Callable[[], None]) -> None:
        def op(co):
            while True:
                callback()
                yield LIVE

        self._sideload_native(op)

    # ---------------------------------------------------------- schedulability -------
    def _unsafe(self, command: "Command") -> frozenset:
        if robot_state.get_fetcher().is_enabled():
            return frozenset()
        return frozenset(m for m in command.requirements()
                         if not m.controllable_during_disabled())

    def is_schedulable(self, command: "Command") -> ScheduleResult:
        if command is None:
            raise TypeError("command cannot be None")
        rid = self.run_id(command)
        if rid != 0:
            return AlreadyRunning(command, rid)
        unsafe = self._unsafe(command)
        if unsafe:
            return RequiresUnsafeMechanisms(command, unsafe)
        ancestry = self._ancestry_of(self._current_state())
        return self._is_schedulable_from_priority(command, ancestry)

    def _is_schedulable_binding(self, binding: Binding) -> ScheduleResult:
        command = binding.command
        rid = self.run_id(command)
        if rid != 0:
            return AlreadyRunning(command, rid)
        unsafe = self._unsafe(command)
        if unsafe:
            return RequiresUnsafeMechanisms(command, unsafe)
        if isinstance(binding.scope, ForCommand):
            ancestry = self._ancestry_of(self._running.get(binding.scope.command))
        else:
            ancestry = self._ancestry_of(self._current_state())
        return self._is_schedulable_from_priority(command, ancestry)

    def _ancestry_of(self, state: CommandState | None) -> set["Command"]:
        out: set["Command"] = set()
        while state is not None:
            out.add(state.command)
            state = self._running.get(state.parent) if state.parent is not None else None
        return out

    def _is_schedulable_from_priority(self, command: "Command",
                                      ancestry: set["Command"]) -> ScheduleResult:
        conflict = self._lower_priority_than_conflicting(command, ancestry,
                                                         self._running.values())
        if conflict is not None:
            return LowerPriorityThanRunningCommand(command, conflict)
        conflict = self._lower_priority_than_conflicting(command, ancestry,
                                                         self._queued.values())
        if conflict is not None:
            return LowerPriorityThanQueuedCommand(command, conflict)
        return Success(command, 0)

    def _lower_priority_than_conflicting(self, command: "Command", ancestry: set["Command"],
                                         check_against: Iterable[CommandState]):
        max_prio = command.priority()
        for a in ancestry:
            max_prio = max(max_prio, a.priority())
        for st in check_against:
            if st.command in ancestry:
                continue
            if st.command.conflicts_with(command) and max_prio < self._effective_priority(st):
                return st.command
        return None

    def _effective_priority(self, state: CommandState) -> int:
        best = state.command.priority()
        parent = state.parent
        while parent is not None:
            best = max(best, parent.priority())
            pst = self._running.get(parent)
            parent = pst.parent if pst is not None else None
        return best

    # ---------------------------------------------------------------- schedule -------
    def schedule(self, command: "Command") -> ScheduleResult:
        """Schedule a command. Called inside a running command, the command becomes its
        child and starts running immediately; otherwise it is queued for the next run()."""
        if command is None:
            raise TypeError("command cannot be None")
        scope = BindingScope.create_narrowest_scope(self)
        binding = Binding(scope, BindingType.IMMEDIATE, command, capture_site(2))
        return self._schedule_binding(binding)

    def _schedule_binding(self, binding: Binding) -> ScheduleResult:
        command = binding.command
        result = self._is_schedulable_binding(binding)
        if not isinstance(result, Success):
            return result
        if not isinstance(binding.scope, ForCommand):
            self._active_bindings[command] = binding
        self._evict_conflicting_on_deck(command)
        parent = binding.scope.command if isinstance(binding.scope, ForCommand) \
            else self.current_command()
        state = CommandState(command, parent, self._new_coroutine(), binding)
        if self._listeners:
            self._emit(Scheduled(command, get_time_ns()))
        current = self._current_state()
        if current is not None:
            self._evict_conflicting_running(state)
            self._running[command] = state
            self._run_command(state)
            # The child may have canceled us (directly or via its composition root):
            # unwind the parent body right here instead of letting it keep running.
            if current.doomed:
                raise Unwind()
        else:
            self._queued[command] = state
        return Success(command, state.id)

    def _evict_conflicting_on_deck(self, command: "Command") -> None:
        for queued_cmd in list(self._queued):
            if queued_cmd.conflicts_with(command):
                self._release_binding(self._queued.pop(queued_cmd))
                self._emit(Interrupted(queued_cmd, get_time_ns(), command))
                self._emit(Canceled(queued_cmd, get_time_ns()))

    def _evict_conflicting_running(self, incoming: CommandState) -> None:
        roots: list[CommandState] = []
        for st in list(self._running.values()):
            if not incoming.command.conflicts_with(st.command):
                continue
            if self._is_ancestor_of(st.command, incoming):
                continue
            root = st
            while root.parent is not None and root.parent is not incoming.parent:
                nxt = self._running.get(root.parent)
                if nxt is None:
                    break
                root = nxt
            if root not in roots:
                roots.append(root)
        for root in roots:
            self._emit(Interrupted(root.command, get_time_ns(), incoming.command))
            self._cancel(root.command)

    def _is_ancestor_of(self, ancestor: "Command", state: CommandState) -> bool:
        if state.parent is None:
            return False
        if ancestor not in self._running:
            return False
        if state.parent is ancestor:
            return True
        pst = self._running.get(state.parent)
        return pst is not None and self._is_ancestor_of(ancestor, pst)

    # ------------------------------------------------------------------ cancel -------
    def cancel(self, command: "Command") -> None:
        """Cancel a command and all of its descendants.

        Canceling the command that is currently executing raises an internal exception
        that unwinds its body immediately (Java: ``requestCancellation`` yields)."""
        if command is None:
            return
        current = self._current_state()
        if current is not None and command is current.command:
            raise CancellationRequested()
        self._cancel(command)
        current = self._current_state()
        if current is not None and current.doomed:
            raise Unwind()

    def _cancel(self, command: "Command") -> None:
        # Every step runs even if an earlier one raises (cleanup in the body's `finally`,
        # a hook, a child's cancellation); the first error is re-raised at the end.
        errors: list[BaseException] = []
        state = self._running.pop(command, None)
        running = state is not None
        queued_state = self._queued.pop(command, None)
        queued = queued_state is not None
        if queued_state is not None:
            self._release_binding(queued_state)
        if state is not None:
            self._teardown(state, errors)
        if running or queued:
            _guard(errors, self._emit, Canceled(command, get_time_ns()))
        _guard(errors, self._remove_orphaned_children, command)
        _raise_first(errors)

    def _teardown(self, state: CommandState, errors: list[BaseException]) -> None:
        """Stop a canceled run (already removed from ``_running``) and run its hooks."""
        self._release_binding(state)
        if state.on_stack:
            state.doomed = True  # unwinds when control returns to it
        else:
            _guard(errors, self._close_task, state)
        _guard(errors, state.command.on_exit)
        _guard(errors, state.command.on_cancel)

    def cancel_all(self) -> None:
        errors: list[BaseException] = []
        for cmd in list(self._queued):
            self._release_binding(self._queued.pop(cmd))
            _guard(errors, self._emit, Canceled(cmd, get_time_ns()))
        for cmd, st in list(self._running.items()):
            if self._running.get(cmd) is not st:
                continue
            del self._running[cmd]
            self._teardown(st, errors)
            _guard(errors, self._emit, Canceled(cmd, get_time_ns()))
        _raise_first(errors)
        current = self._current_state()
        if current is not None and current.doomed:
            raise Unwind()  # called from inside a command: stop its body here, like cancel()

    def _remove_orphaned_children(self, parent: "Command") -> None:
        errors: list[BaseException] = []
        for cmd in [c for c, s in self._running.items() if s.parent is parent]:
            _guard(errors, self._cancel, cmd)
        _raise_first(errors)

    # --------------------------------------------------------------------- run -------
    def run(self) -> None:
        """One scheduler cycle. Call this once per robot loop (e.g. in robot_periodic)."""
        start = get_time_ns()
        self._cancel_stale_bindings()
        self._unbind_stale_triggers()
        self._cancel_commands_that_cannot_run_in_disabled()
        self._run_periodic_sideloads()
        self._event_loop.poll()
        self._schedule_default_commands()
        self._promote_scheduled_commands()
        self._run_commands()
        self._last_run_time_ms = (get_time_ns() - start) / 1e6

    def _cancel_stale_bindings(self) -> None:
        stale = [b for b in self._active_bindings.values() if not b.scope.active()]
        for b in stale:
            if self._active_bindings.get(b.command) is b:  # an earlier cancel may have ended it
                self._cancel(b.command)
            self._release_binding_of(b)

    def _release_binding(self, state: CommandState) -> None:
        self._release_binding_of(state.binding)

    def _release_binding_of(self, binding: Binding) -> None:
        if self._active_bindings.get(binding.command) is binding:
            del self._active_bindings[binding.command]

    def _unbind_stale_triggers(self) -> None:
        for t in list(self._bound_triggers):
            if not t._is_scope_active():
                self._bound_triggers.discard(t)

    def _cancel_commands_that_cannot_run_in_disabled(self) -> None:
        if robot_state.get_fetcher().is_enabled():
            return
        to_cancel = []
        for st in self._running.values():
            if any(not m.controllable_during_disabled() for m in st.command.requirements()):
                to_cancel.append(self._get_root(st.command))
        for st in self._queued.values():
            if any(not m.controllable_during_disabled() for m in st.command.requirements()):
                to_cancel.append(st.command)
        for c in to_cancel:
            self._cancel(c)

    def _add_bound_trigger(self, trigger: "Trigger") -> None:
        self._bound_triggers.add(trigger)

    def _remove_bound_trigger(self, trigger: "Trigger") -> None:
        self._bound_triggers.discard(trigger)

    def _promote_scheduled_commands(self) -> None:
        for st in list(self._queued.values()):
            self._evict_conflicting_running(st)
        for cmd, st in self._queued.items():
            self._running[cmd] = st
        self._queued.clear()

    def _run_periodic_sideloads(self) -> None:
        for st in list(self._sideloads):
            if not st.scope.active():
                self._sideloads.remove(st)
                self._close_task(st)
                continue
            if not st.park.should_mount():
                continue
            outcome = self._step_sideload(st)
            self._finish_sideload(st, outcome)

    def _step_sideload(self, st: _SideloadState) -> Outcome:
        prev = self._mounted
        self._set_mounted(st.coroutine)
        try:
            return self._mount_step(st)
        finally:
            self._set_mounted(prev)

    def _finish_sideload(self, st: _SideloadState, outcome: Any) -> None:
        if type(outcome) is not Outcome:
            st.park = outcome if isinstance(outcome, Park) else self._foreign_yield(outcome)
            return
        st.finished = True
        if st in self._sideloads:
            self._sideloads.remove(st)
        if outcome.kind is Kind.ERROR:
            raise outcome.value

    def _run_commands(self) -> None:
        # Reverse order: children (scheduled later) run before their parents, so a parent
        # awaiting a child that finishes this cycle resumes in the same cycle.
        for st in reversed(list(self._running.values())):
            self._run_command(st)

    # --------------------------------------------------------- mounting a command ----
    def _can_mount(self, state: CommandState) -> bool:
        if self._running.get(state.command) is not state:
            return False  # canceled earlier in this cycle (or replaced by a new run)
        return state.park.should_mount()

    def _pre_mount(self, state: CommandState):
        previous_mounted = self._mounted
        self._ancestry.append(state)
        state.on_stack = True
        self._set_mounted(state.coroutine)
        if self._listeners:
            self._emit(Mounted(state.command, get_time_ns()))
        return previous_mounted, _perf_ns()

    def _run_command(self, state: CommandState) -> None:
        if not self._can_mount(state):
            return
        token = self._pre_mount(state)
        outcome = self._mount_step(state)
        self._post_mount(state, token, outcome)

    def _post_mount(self, state: CommandState, token, outcome: Any) -> None:
        previous_mounted, start = token
        ms = (_perf_ns() - start) / 1e6
        state.last_runtime_ms = ms
        state.total_runtime_ms += ms
        state.on_stack = False
        ancestry = self._ancestry
        if ancestry and ancestry[-1] is state:
            ancestry.pop()
        self._set_mounted(previous_mounted)
        if type(outcome) is not Outcome:  # hot path: the command suspended
            if state.doomed:
                self._close_task(state)
                state.finished = True
                return
            state.park = outcome if isinstance(outcome, Park) else self._foreign_yield(outcome)
            if self._listeners:
                self._emit(Yielded(state.command, get_time_ns()))
            return
        command = state.command
        kind = outcome.kind

        if kind is Kind.ERROR:
            state.finished = True
            self._handle_command_exception(state, outcome.value)  # raises
        if state.doomed:
            # Already canceled (hooks + events done) while it was executing.
            state.finished = True
            return
        if kind is Kind.DONE:
            state.finished = True
            self._handle_completion(state)
        elif kind is Kind.CANCEL_REQUESTED:
            state.finished = True
            self._cancel(command)
        elif kind is Kind.FORK_FAILED:
            state.finished = True
            root = self._get_root(command)
            self._emit(ForkFailure(command, get_time_ns(), tuple(outcome.value.get_failed_commands())))
            self._cancel(root)
        elif kind is Kind.UNWOUND:
            state.finished = True

    def _to_park(self, value: Any) -> Park:
        if isinstance(value, Park):
            return value
        return self._foreign_yield(value)

    def _foreign_yield(self, value: Any) -> Park:
        raise TypeError(
            f"A command yielded {value!r}. Commands may only suspend through their coroutine "
            f"(or, on the asyncio runtime, by awaiting asyncio awaitables)."
        )

    def _handle_completion(self, state: CommandState) -> None:
        command = state.command
        self._release_binding(state)
        command.on_exit()
        if self._listeners:
            self._emit(Completed(command, get_time_ns()))
        if self._running.get(command) is state:
            del self._running[command]
        self._remove_orphaned_children(command)

    def _handle_command_exception(self, state: CommandState, error: BaseException) -> None:
        command = state.command
        root = self._get_root(command)
        self._release_binding(state)
        if self._running.get(command) is state:
            del self._running[command]
        site = state.binding.site
        if site is not None and not getattr(error, "_commands3_traced", False):
            try:
                error._commands3_traced = True  # type: ignore[attr-defined]
            except Exception:
                pass
            error.add_note("=== Command Binding Trace ===\n" + "".join(site.format()).rstrip())
        # on_exit runs "whenever the command stops running, for any reason" (Java skips it
        # here); hook failures are attached to the original error, which wins.
        errors: list[BaseException] = []
        _guard(errors, command.on_exit)
        _guard(errors, self._emit, CompletedWithError(command, get_time_ns(), error))
        _guard(errors, self._remove_orphaned_children, command)
        if root is not None and root is not command:
            _guard(errors, self._cancel, root)
        for e in errors:
            error.add_note(f"while handling this error, cleanup also raised {e!r}")
        raise error

    # ------------------------------------------------------------------ helpers ------
    def _current_state(self) -> CommandState | None:
        return self._ancestry[-1] if self._ancestry else None

    def current_command(self) -> "Command | None":
        st = self._current_state()
        return st.command if st is not None else None

    def _schedule_default_commands(self) -> None:
        for mech in list(self._default_command_bindings):
            self._process_default_commands(mech)

    def _process_default_commands(self, mechanism: "Mechanism") -> None:
        bindings = self._default_command_bindings[mechanism]
        for b in list(bindings):
            if not b.scope.active():
                self._cancel(b.command)
                bindings.remove(b)
        if not bindings:
            return
        for b in bindings[:-1]:
            self._cancel(b.command)
        if not robot_state.get_fetcher().is_enabled() and \
                not mechanism.controllable_during_disabled():
            return
        for cmd in self._running:
            if cmd.requires(mechanism):
                return
        for st in self._queued.values():
            if st.command.requires(mechanism):
                return
        self._schedule_binding(bindings[-1])

    def _get_root(self, command: "Command") -> "Command":
        root = command
        parent = self.get_parent_of(command)
        while parent is not None:
            root = parent
            parent = self.get_parent_of(parent)
        return root

    # ------------------------------------------------------------- introspection -----
    def is_running(self, command: "Command") -> bool:
        return command in self._running

    def is_scheduled(self, command: "Command") -> bool:
        return command in self._queued

    def is_scheduled_or_running(self, command: "Command") -> bool:
        return command in self._queued or command in self._running

    def get_running_commands(self) -> list["Command"]:
        return list(self._running)

    def get_running_commands_for(self, mechanism: "Mechanism") -> list["Command"]:
        return [c for c in self._running if c.requires(mechanism)]

    def get_queued_commands(self) -> list["Command"]:
        return list(self._queued)

    def get_default_event_loop(self) -> EventLoop:
        return self._event_loop

    def get_parent_of(self, command: "Command") -> "Command | None":
        st = self._running.get(command)
        return st.parent if st is not None else None

    def last_command_runtime_ms(self, command: "Command") -> float:
        st = self._running.get(command)
        return st.last_runtime_ms if st is not None else -1.0

    def total_runtime_ms(self, command: "Command") -> float:
        st = self._running.get(command)
        return st.total_runtime_ms if st is not None else -1.0

    def run_id(self, command: "Command") -> int:
        st = self._running.get(command)
        if st is not None:
            return st.id
        st = self._queued.get(command)
        return st.id if st is not None else 0

    def last_runtime_ms(self) -> float:
        return self._last_run_time_ms

    # ------------------------------------------------------------------- events ------
    def add_event_listener(self, listener: Callable[[SchedulerEvent], None]) -> None:
        if listener is None:
            raise TypeError("listener cannot be None")
        if listener not in self._listeners:
            self._listeners.append(listener)

    def _emit(self, event: SchedulerEvent) -> None:
        for listener in self._listeners:
            listener(event)

    # ---------------------------------------------------------------- telemetry ------
    def snapshot(self) -> dict:
        from .telemetry import scheduler_snapshot

        return scheduler_snapshot(self)

    def to_protobuf(self) -> bytes:
        from .telemetry import pack_scheduler

        return pack_scheduler(self)


def _guard(errors: list[BaseException], fn: Callable[..., Any], *args: Any) -> None:
    """Call ``fn``; record (rather than propagate) anything it raises."""
    try:
        fn(*args)
    except BaseException as e:
        errors.append(e)


def _raise_first(errors: list[BaseException]) -> None:
    if not errors:
        return
    first = errors[0]
    for e in errors[1:]:
        if e is not first:
            first.add_note(f"cleanup also raised {e!r}")
    raise first
