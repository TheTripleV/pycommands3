"""Port of Command.java, StagedCommandBuilder and the builder stages.

A command's ``run(coroutine)`` is interpreted by the scheduler's front-end: it may return a
generator (generators front-end), a coroutine (async/await), an async generator (async
generators) or simply run to completion while switching greenlets (greenlet front-end).

Framework-internal commands (groups, state machines, wait commands, idle) set
``_native = True``; their ``run`` returns a Park-yielding primitive op that every runtime can
drive regardless of which front-end the user picked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:
    from .groups import ParallelGroupBuilder, SequentialGroupBuilder
    from .mechanism import Mechanism

DEFAULT_PRIORITY = 0
LOWEST_PRIORITY = -(2**31)
HIGHEST_PRIORITY = 2**31 - 1
_UNSET = object()


def _fmt_seconds(seconds: float) -> str:
    return f"{float(seconds)} s"


class Command:
    """Base class for commands. Subclasses implement ``run``, ``name`` and ``requirements``."""

    DEFAULT_PRIORITY = DEFAULT_PRIORITY
    LOWEST_PRIORITY = LOWEST_PRIORITY
    HIGHEST_PRIORITY = HIGHEST_PRIORITY

    _native: bool = False

    # ------------------------------------------------------------------- interface -----
    if TYPE_CHECKING:
        run: Callable[..., Any]
    else:
        def run(self, coroutine: Any = None) -> Any:
            raise NotImplementedError

    def on_cancel(self) -> None:
        """Called when the command is canceled before it completes naturally."""

    def on_exit(self) -> None:
        """Called whenever the command stops running, for any reason."""

    def name(self) -> str:
        raise NotImplementedError

    def requirements(self) -> "frozenset[Mechanism] | set[Mechanism]":
        raise NotImplementedError

    def priority(self) -> int:
        return DEFAULT_PRIORITY

    def is_lower_priority_than(self, other: "Command") -> bool:
        if other is None:
            raise TypeError("other cannot be None")
        return self.priority() < other.priority()

    def requires(self, mechanism: "Mechanism") -> bool:
        return mechanism in self.requirements()

    def conflicts_with(self, other: "Command") -> bool:
        if other is None:
            raise TypeError("other cannot be None")
        mine = self.requirements()
        return bool(mine) and not mine.isdisjoint(other.requirements())

    def __repr__(self) -> str:
        try:
            return self.name()
        except NotImplementedError:
            return object.__repr__(self)

    # ------------------------------------------------------------------ decorators -----
    def with_timeout(self, seconds: float) -> "Command":
        if seconds is None:
            raise TypeError("timeout cannot be None")
        t = _fmt_seconds(seconds)
        return Command.race(self, Command.wait_for(seconds).named(f"Timeout: {t}")).named(
            f"{self.name()} [{t} timeout]"
        )

    def until(self, end_condition: Callable[[], bool]) -> "ParallelGroupBuilder":
        if end_condition is None:
            raise TypeError("endCondition cannot be None")
        from .groups import ParallelGroupBuilder

        return ParallelGroupBuilder().optional(
            self, Command.wait_until(end_condition).named("Until Condition")
        )

    def and_then(self, next: "Command") -> "SequentialGroupBuilder":
        from .groups import SequentialGroupBuilder

        return SequentialGroupBuilder().and_then(self).and_then(next)

    def along_with(self, *parallel: "Command") -> "ParallelGroupBuilder":
        from .groups import ParallelGroupBuilder

        return ParallelGroupBuilder().requiring(self).requiring(*parallel)

    def race_with(self, *parallel: "Command") -> "ParallelGroupBuilder":
        from .groups import ParallelGroupBuilder

        return ParallelGroupBuilder().optional(self).optional(*parallel)

    # ------------------------------------------------------------------- factories -----
    @staticmethod
    def no_requirements(body: Callable[..., Any]) -> "NeedsNameBuilderStage":
        return StagedCommandBuilder().no_requirements().executing(body)

    @staticmethod
    def requiring(*mechanisms: "Mechanism | Iterable[Mechanism]") -> "NeedsExecutionBuilderStage":
        return StagedCommandBuilder().requiring(*mechanisms)

    @staticmethod
    def parallel(*commands: "Command") -> "ParallelGroupBuilder":
        from .groups import ParallelGroupBuilder

        return ParallelGroupBuilder().requiring(*commands)

    @staticmethod
    def race(*commands: "Command") -> "ParallelGroupBuilder":
        from .groups import ParallelGroupBuilder

        return ParallelGroupBuilder().optional(*commands)

    @staticmethod
    def sequence(*commands: "Command") -> "SequentialGroupBuilder":
        from .groups import SequentialGroupBuilder

        return SequentialGroupBuilder().and_then(*commands)

    @staticmethod
    def wait_until(condition: Callable[[], bool]) -> "NeedsNameBuilderStage":
        if condition is None:
            raise TypeError("condition cannot be None")
        return StagedCommandBuilder().no_requirements()._executing_native(
            lambda co: co.op_wait_until(condition)
        )

    @staticmethod
    def wait_for(seconds: float) -> "NeedsNameBuilderStage":
        if seconds is None:
            raise TypeError("duration cannot be None")
        return StagedCommandBuilder().no_requirements()._executing_native(
            lambda co: co.op_wait(seconds)
        )


# ======================================================================================
# Staged builder
# ======================================================================================


def _flatten_mechanisms(args: tuple, what: str) -> list["Mechanism"]:
    from .mechanism import Mechanism

    out = []
    for i, a in enumerate(args):
        if a is None:
            raise TypeError(f"{what}[{i}] cannot be None")
        if isinstance(a, Mechanism):
            out.append(a)
        else:
            for j, m in enumerate(a):
                if m is None:
                    raise TypeError(f"{what}[{j}] cannot be None")
                out.append(m)
    return out


class _BuilderBackedCommand(Command):
    def __init__(self, b: "StagedCommandBuilder"):
        self._requirements = frozenset(b._requirements)
        self._impl = b._impl
        self._on_cancel = b._on_cancel or (lambda: None)
        self._on_exit = b._on_exit or (lambda: None)
        self._name = b._name
        self._priority = b._priority
        self._native = b._native

    def run(self, coroutine: Any = _UNSET) -> Any:
        assert self._impl is not None
        if coroutine is _UNSET:
            return self._impl()
        return self._impl(coroutine)

    def on_cancel(self) -> None:
        self._on_cancel()

    def on_exit(self) -> None:
        self._on_exit()

    def name(self) -> str:
        return self._name  # type: ignore[return-value]

    def requirements(self) -> frozenset:
        return self._requirements

    def priority(self) -> int:
        return self._priority


class StagedCommandBuilder:
    def __init__(self) -> None:
        self._requirements: list = []
        self._impl: Callable[..., Any] | None = None
        self._on_cancel: Callable[[], None] | None = None
        self._on_exit: Callable[[], None] | None = None
        self._name: str | None = None
        self._priority = DEFAULT_PRIORITY
        self._end_condition: Callable[[], bool] | None = None
        self._built: Command | None = None
        self._native = False
        self._needs_execution = NeedsExecutionBuilderStage(self)
        self._needs_name = NeedsNameBuilderStage(self)

    def _throw_if_built(self) -> None:
        if self._built is not None:
            raise RuntimeError("Command builders cannot be reused")

    def no_requirements(self) -> "NeedsExecutionBuilderStage":
        self._throw_if_built()
        return self._needs_execution

    def requiring(self, *mechanisms: "Mechanism | Iterable[Mechanism]") -> "NeedsExecutionBuilderStage":
        self._throw_if_built()
        if not mechanisms:
            raise TypeError("requirement cannot be None")
        for m in _flatten_mechanisms(mechanisms, "requirements"):
            if m not in self._requirements:
                self._requirements.append(m)
        return self._needs_execution


class NeedsExecutionBuilderStage:
    def __init__(self, b: StagedCommandBuilder):
        self._b = b

    def requiring(self, *mechanisms: "Mechanism | Iterable[Mechanism]") -> "NeedsExecutionBuilderStage":
        self._b.requiring(*mechanisms)
        return self

    def executing(self, impl: Callable[..., Any]) -> "NeedsNameBuilderStage":
        self._b._throw_if_built()
        if impl is None:
            raise TypeError("impl cannot be None")
        self._b._impl = impl
        return self._b._needs_name

    def _executing_native(self, op_factory: Callable[[Any], Any]) -> "NeedsNameBuilderStage":
        stage = self.executing(op_factory)
        self._b._native = True
        return stage


class NeedsNameBuilderStage:
    def __init__(self, b: StagedCommandBuilder):
        self._b = b

    def when_canceled(self, on_cancel: Callable[[], None] | None) -> "NeedsNameBuilderStage":
        self._b._throw_if_built()
        self._b._on_cancel = on_cancel
        return self

    def when_exited(self, on_exit: Callable[[], None] | None) -> "NeedsNameBuilderStage":
        self._b._throw_if_built()
        self._b._on_exit = on_exit
        return self

    def with_priority(self, priority: int) -> "NeedsNameBuilderStage":
        self._b._throw_if_built()
        self._b._priority = priority
        return self

    def until(self, end_condition: Callable[[], bool] | None) -> "NeedsNameBuilderStage":
        self._b._throw_if_built()
        self._b._end_condition = end_condition
        return self

    def named(self, name: str) -> Command:
        b = self._b
        b._throw_if_built()
        if name is None:
            raise TypeError("name cannot be None")
        b._name = name
        command = _BuilderBackedCommand(b)
        if b._end_condition is None:
            b._built = command
        else:
            from .groups import ParallelGroupBuilder

            b._built = ParallelGroupBuilder().requiring(command).until(b._end_condition).named(name)
        return b._built
