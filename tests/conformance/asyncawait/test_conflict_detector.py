"""Port of ConflictDetectorTest.java."""

import pytest

from commands3._common.api import Command, Conflict, ConflictDetector
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism


def test_empty_input_has_no_conflicts():
    assert ConflictDetector.find_all_conflicts([]) == []


def test_single_input_has_no_conflicts(scheduler):
    mech = DummyMechanism("Mech", scheduler)
    command = Command.requiring(mech).executing(Coroutine.park).named("Command")
    assert ConflictDetector.find_all_conflicts([command]) == []


def test_command_does_not_conflict_with_self(scheduler):
    mech = DummyMechanism("Mech", scheduler)
    command = Command.requiring(mech).executing(Coroutine.park).named("Command")
    assert ConflictDetector.find_all_conflicts([command, command]) == []


def test_detect_many_conflicts(scheduler):
    m1 = DummyMechanism("Mech 1", scheduler)
    m2 = DummyMechanism("Mech 2", scheduler)
    c1 = Command.requiring(m1, m2).executing(Coroutine.park).named("Command1")
    c2 = Command.requiring(m1).executing(Coroutine.park).named("Command2")
    c3 = Command.requiring(m2).executing(Coroutine.park).named("Command3")
    c4 = Command.requiring(m2, m1).executing(Coroutine.park).named("Command4")
    all_commands = [c1, c2, c3, c4]
    conflicts = ConflictDetector.find_all_conflicts(all_commands)
    assert conflicts == [
        Conflict(c1, c2, frozenset({m1})),
        Conflict(c1, c3, frozenset({m2})),
        Conflict(c1, c4, frozenset({m1, m2})),
        Conflict(c2, c4, frozenset({m1})),
        Conflict(c3, c4, frozenset({m2})),
    ]
    with pytest.raises(ValueError) as e:
        ConflictDetector.throw_if_conflicts(all_commands)
    assert str(e.value) == (
        "Commands running in parallel cannot share requirements: "
        "Command1 and Command2 both require Mech 1; "
        "Command1 and Command3 both require Mech 2; "
        "Command1 and Command4 both require Mech 1, Mech 2; "
        "Command2 and Command4 both require Mech 1; "
        "Command3 and Command4 both require Mech 2"
    )
