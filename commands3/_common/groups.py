"""Port of SequentialGroup, ParallelGroup and their builders."""

from __future__ import annotations

from typing import Callable

from .command import DEFAULT_PRIORITY, Command
from .results import ConflictDetector


def _check(commands, what: str) -> list[Command]:
    if commands is None:
        raise TypeError(f"{what} cannot be None")
    out = list(commands)
    for i, c in enumerate(out):
        if c is None:
            raise TypeError(f"{what}[{i}] cannot be None")
    return out


class SequentialGroup(Command):
    _native = True

    def __init__(self, name: str, commands):
        if name is None:
            raise TypeError("name cannot be None")
        self._name = name
        self._commands = _check(commands, "commands")
        reqs: set = set()
        for c in self._commands:
            reqs |= set(c.requirements())
        self._requirements = frozenset(reqs)
        self._priority = max((c.priority() for c in self._commands), default=DEFAULT_PRIORITY)

    def run(self, coroutine):
        for command in self._commands:
            yield from coroutine.op_await(command)

    def name(self) -> str:
        return self._name

    def requirements(self) -> frozenset:
        return self._requirements

    def priority(self) -> int:
        return self._priority

    def get_commands(self) -> list[Command]:
        return self._commands

    def __repr__(self) -> str:
        return f"SequentialGroup[name={self._name}]"


class ParallelGroup(Command):
    _native = True

    def __init__(self, name: str, required_commands, optional_commands):
        if name is None:
            raise TypeError("name cannot be None")
        required = _check(required_commands, "requiredCommands")
        optional = _check(optional_commands, "optionalCommands")
        all_commands: list[Command] = []
        for c in [*required, *optional]:
            if c not in all_commands:
                all_commands.append(c)
        ConflictDetector.throw_if_conflicts(all_commands)
        self._name = name
        self._required = list(dict.fromkeys(required))
        self._optional = list(dict.fromkeys(optional))
        reqs: set = set()
        for c in all_commands:
            reqs |= set(c.requirements())
        self._requirements = frozenset(reqs)
        self._priority = max((c.priority() for c in all_commands), default=DEFAULT_PRIORITY)

    def run(self, coroutine):
        coroutine.fork(self._optional)
        if not self._required:
            yield from coroutine.op_await_any(self._optional)
        else:
            yield from coroutine.op_await_all(self._required)

    def name(self) -> str:
        return self._name

    def requirements(self) -> frozenset:
        return self._requirements

    def priority(self) -> int:
        return self._priority

    def get_required_commands(self) -> list[Command]:
        return self._required

    def get_optional_commands(self) -> list[Command]:
        return self._optional

    def __repr__(self) -> str:
        return f"ParallelGroup[name={self._name}]"


class SequentialGroupBuilder:
    def __init__(self) -> None:
        self._steps: list[Command] = []
        self._end_condition: Callable[[], bool] | None = None

    def and_then(self, *next_commands: Command) -> "SequentialGroupBuilder":
        if not next_commands:
            raise TypeError("next cannot be None")
        for i, c in enumerate(next_commands):
            if c is None:
                raise TypeError(f"nextCommands[{i}] cannot be None")
        self._steps.extend(next_commands)
        return self

    def until(self, end_condition: Callable[[], bool] | None) -> "SequentialGroupBuilder":
        self._end_condition = end_condition
        return self

    def named(self, name: str) -> Command:
        return self._named(name, False)

    def _named(self, name: str, automatic: bool) -> Command:
        seq = SequentialGroup(name, self._steps)
        if self._end_condition is None:
            return seq
        out = ParallelGroupBuilder().optional(
            seq, Command.wait_until(self._end_condition).named("Until Condition")
        )
        return out.with_automatic_name() if automatic else out.named(name)

    def with_automatic_name(self) -> Command:
        return self._named(" -> ".join(c.name() for c in self._steps), True)


class ParallelGroupBuilder:
    def __init__(self) -> None:
        self._optional: list[Command] = []
        self._required: list[Command] = []
        self._end_condition: Callable[[], bool] | None = None

    @staticmethod
    def _add(target: list[Command], commands: tuple, what: str) -> None:
        for i, c in enumerate(commands):
            if c is None:
                raise TypeError(f"commands[{i}] cannot be None ({what})")
            if c not in target:
                target.append(c)

    def optional(self, *commands: Command) -> "ParallelGroupBuilder":
        self._add(self._optional, commands, "optional")
        return self

    def requiring(self, *commands: Command) -> "ParallelGroupBuilder":
        self._add(self._required, commands, "requiring")
        return self

    def along_with(self, command: Command) -> "ParallelGroupBuilder":
        return self.requiring(command)

    def until(self, condition: Callable[[], bool] | None) -> "ParallelGroupBuilder":
        self._end_condition = condition
        return self

    def named(self, name: str) -> ParallelGroup:
        return self._named(name, False)

    def _named(self, name: str, automatic: bool) -> ParallelGroup:
        if name is None:
            raise TypeError("name cannot be None")
        group = ParallelGroup(name, self._required, self._optional)
        if self._end_condition is None:
            return group
        out = ParallelGroupBuilder().optional(
            group, Command.wait_until(self._end_condition).named("Until Condition")
        )
        return out.with_automatic_name() if automatic else out.named(name)

    def with_automatic_name(self) -> ParallelGroup:
        required = "(" + " & ".join(c.name() for c in self._required) + ")"
        optional = "(" + " | ".join(c.name() for c in self._optional) + ")"
        if not self._required:
            return self._named(optional, True)
        if not self._optional:
            return self._named(required, True)
        return self._named(f"[{required} * {optional}]", True)
