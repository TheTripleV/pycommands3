"""Port of StateMachineTest.java."""

import pytest

from commands3._common.api import (
    DEFAULT_PRIORITY,
    HIGHEST_PRIORITY,
    Canceled,
    Command,
    Mounted,
    Scheduled,
    StateMachine,
    Trigger,
    Yielded,
)
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism, Ref


def park(name):
    return Command.no_requirements(Coroutine.park).named(name)


def yield_once(name):
    return Command.no_requirements(Coroutine.yield_).named(name)


def names(scheduler):
    return [c.name() for c in scheduler.get_running_commands()]


# --------------------------------------------------------------- command attributes ----
def test_name():
    assert StateMachine("State Machine").name() == "State Machine"


def test_default_priority_is_zero():
    assert StateMachine("State Machine").priority() == DEFAULT_PRIORITY


def test_set_priority_is_respected():
    sm = StateMachine("State Machine")
    sm.set_priority(HIGHEST_PRIORITY)
    assert sm.priority() == HIGHEST_PRIORITY


def test_requirements_empty_by_default():
    assert StateMachine("State Machine").requirements() == set()


def test_requirements_are_not_inherited(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    sm = StateMachine("State Machine")
    sm.add_state(mech.run(Coroutine.park).named("Command"))
    assert sm.requirements() == set()


# ----------------------------------------------------------------------- behaviour -----
def test_errors_without_initial_state(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    sm = StateMachine("State Machine")
    s1 = sm.add_state(mech.run(Coroutine.park).named("Command 1"))
    s2 = sm.add_state(mech.run(Coroutine.park).named("Command 2"))
    s1.switch_to(s2).when_complete()
    scheduler.schedule(sm)
    with pytest.raises(RuntimeError) as e:
        scheduler.run()
    assert str(e.value) == ("State Machine does not have an initial state. "
                            "Use .set_initial_state() to provide one.")
    assert not scheduler.is_running(sm)


def test_initial_state_can_be_overridden(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    c1 = mech.run(Coroutine.park).named("Command 1")
    c2 = mech.run(Coroutine.park).named("Command 2")
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)
    sm.set_initial_state(s2)
    s2.switch_to(s1).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert scheduler.is_running(c2)
    assert not scheduler.is_running(c1)


def test_transitions(scheduler):
    a, b = Ref(False), Ref(False)
    mech = DummyMechanism("Mechanism", scheduler)
    c1, c2, c3 = (mech.run(Coroutine.park).named(f"Command {i}") for i in (1, 2, 3))
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when(a.get)
    s2.switch_to(s3).when(b.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c1)
    assert not scheduler.is_running(c2) and not scheduler.is_running(c3)
    a.set(True)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c2)
    assert not scheduler.is_running(c1) and not scheduler.is_running(c3)
    b.set(True)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c3)
    assert not scheduler.is_running(c1) and not scheduler.is_running(c2)


def test_transitions_if_condition_is_already_true_when_entered(scheduler):
    c1, c2 = park("Command 1"), park("Command 2")
    signal = Ref(False)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when(signal.get)
    signal.set(True)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert not scheduler.is_running(c1)
    assert scheduler.is_running(c2)


def test_command_exits(scheduler):
    signal = Ref(False)

    async def body(co: Coroutine):
        await co.wait_until(signal.get)

    c1 = Command.no_requirements(body).named("Command 1")
    c2 = park("Command 2")
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when_complete()
    s2.exit_state_machine().when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c1)
    signal.set(True)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert not scheduler.is_running(c1)
    assert scheduler.is_running(c2)


def test_state_transitions_to_self(scheduler, events):
    signal = Ref(False)
    init_count = Ref(0)

    async def body(co: Coroutine):
        init_count.increment_and_get()
        await co.park()

    command = Command.no_requirements(body).named("Command")
    sm = StateMachine("State Machine")
    s = sm.add_state(command)
    sm.set_initial_state(s)
    s.switch_to(s).when(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert init_count.get() == 1
    signal.set(True)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert init_count.get() == 2
    assert [(type(e), e.command) for e in events] == [
        (Scheduled, sm), (Mounted, sm), (Scheduled, command), (Mounted, command),
        (Yielded, command), (Yielded, sm), (Mounted, sm), (Canceled, command),
        (Scheduled, command), (Mounted, command), (Yielded, command), (Yielded, sm),
    ]


def test_oneshot_command_transitions_to_self_on_complete(scheduler):
    count = Ref(0)
    command = Command.no_requirements(lambda c: count.increment_and_get()).named("Command")
    sm = StateMachine("State Machine")
    s = sm.add_state(command)
    sm.set_initial_state(s)
    s.switch_to(s).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert count.get() == 1


def test_only_first_explicit_transition_fires(scheduler):
    signal = Ref(False)
    c1, c2, c3 = park("Command 1"), park("Command 2"), park("Command 3")
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when(signal.get)
    s1.switch_to(s3).when(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c1]
    signal.set(True)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c2]


def test_only_last_when_complete_transition_fires(scheduler):
    c1, c2, c3 = yield_once("Command 1"), yield_once("Command 2"), yield_once("Command 3")
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when_complete()
    s1.switch_to(s3).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c1]
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c3]


@pytest.mark.parametrize("and_first", [False, True])
def test_when_complete_and_takes_priority_over_when_complete(scheduler, and_first):
    signal = Ref(False)
    c1, c2, c3 = yield_once("Command 1"), yield_once("Command 2"), yield_once("Command 3")
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    if and_first:
        s1.switch_to(s3).when_complete_and(signal.get)
        s1.switch_to(s2).when_complete()
    else:
        s1.switch_to(s2).when_complete()
        s1.switch_to(s3).when_complete_and(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c1]
    signal.set(True)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c3]


def test_composing_complete(scheduler):
    signal = Ref(False)
    c1, c2 = yield_once("Command 1"), park("Command 2")
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)
    s1.exit_state_machine().when_complete()
    s1.switch_to(s2).when_complete_and(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c1)
    scheduler.run()
    assert not scheduler.is_running(sm)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c1)
    signal.set(True)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert not scheduler.is_running(c1)
    assert scheduler.is_running(c2)


def test_cannot_add_state_after_no_arg_switch_from_any():
    sm = StateMachine("State Machine")
    s1 = sm.add_state(park("Command 1"))
    sm.set_initial_state(s1)
    sm.switch_from_any().to_exit_state_machine().when(lambda: True)
    with pytest.raises(RuntimeError) as e:
        sm.add_state(park("Command 2"))
    assert str(e.value) == ("Cannot add new states to a state machine after switch_from_any() "
                            "has been called with no arguments")


def test_can_add_state_after_explicit_switch_from_any():
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(park("Command 1")), sm.add_state(park("Command 2"))
    sm.switch_from_any(s1, s2).to_exit_state_machine().when(lambda: True)
    s3 = sm.add_state(park("Command 3"))
    sm.set_initial_state(s1)
    s3.switch_to(s1).when_complete()


def test_can_add_state_after_explicit_state_transition():
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(park("Command 1")), sm.add_state(park("Command 2"))
    s1.switch_to(s2).when_complete()
    s3 = sm.add_state(park("Command 3"))
    sm.set_initial_state(s1)
    s2.switch_to(s3).when_complete()


def test_cannot_transition_between_machines():
    a, b = StateMachine("A"), StateMachine("B")
    sa, sb = a.add_state(park("1")), b.add_state(park("2"))
    with pytest.raises(ValueError):
        sa.switch_to(sb)
    with pytest.raises(ValueError):
        a.set_initial_state(sb)


def test_switch_from_any(scheduler):
    c1, c2, c3 = yield_once("Command 1"), park("Command 2"), park("Command 3")
    signal = Ref(False)
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    sm.switch_from_any(s1, s2).to(s3).when(signal.get)
    s1.switch_to(s2).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(c1)
    signal.set(True)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert not scheduler.is_running(c1)
    assert scheduler.is_running(c3)
    scheduler.cancel(sm)
    signal.set(False)
    scheduler.schedule(sm)
    scheduler.run()
    assert names(scheduler) == ["State Machine", "Command 1"]
    scheduler.run()
    assert names(scheduler) == ["State Machine", "Command 2"]
    signal.set(True)
    scheduler.run()
    assert names(scheduler) == ["State Machine", "Command 3"]


def test_switch_to_supplier_when_complete(scheduler):
    count = Ref(0)

    async def body(co: Coroutine):
        count.increment_and_get()
        await co.yield_()

    c1 = Command.no_requirements(body).named("Command 1")
    c2, c3 = park("Command 2"), park("Command 3")
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    s1.switch_to(lambda: s2 if count.get() == 1 else s3).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c1]
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c2]


def test_switch_to_supplier_with_condition(scheduler):
    count = Ref(0)

    async def body(co: Coroutine):
        while True:
            await co.yield_()
            count.increment_and_get()

    c1 = Command.no_requirements(body).named("Command 1")
    c2, c3 = park("Command 2"), park("Command 3")
    sm = StateMachine("State Machine")
    s1, s2, s3 = sm.add_state(c1), sm.add_state(c2), sm.add_state(c3)
    sm.set_initial_state(s1)
    s1.switch_to(lambda: s2 if count.get() == 1 else s3).when(lambda: count.get() == 1)
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c1]
    scheduler.run()
    assert scheduler.get_running_commands() == [sm, c2]


def test_runs_on_enter_for_initial_state(scheduler):
    count = Ref(0)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(park("Command 1")), sm.add_state(park("Command 2"))
    sm.set_initial_state(s1)
    s1.on_enter(count.increment_and_get)
    s1.switch_to(s2).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert count.get() == 1


def test_runs_on_exit_on_transition(scheduler):
    count, signal = Ref(0), Ref(False)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(park("Command 1")), sm.add_state(park("Command 2"))
    sm.set_initial_state(s1)
    s1.on_exit(count.increment_and_get)
    s1.switch_to(s2).when(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    assert count.get() == 0
    signal.set(True)
    scheduler.run()
    assert count.get() == 1


def test_runs_on_exit_when_complete(scheduler):
    c1 = Command.no_requirements(lambda co: None).named("Command 1")
    count = Ref(0)
    sm = StateMachine("State Machine")
    s1 = sm.add_state(c1)
    sm.set_initial_state(s1)
    s1.on_exit(count.increment_and_get)
    scheduler.schedule(sm)
    scheduler.run()
    assert count.get() == 1
    assert not scheduler.is_running(c1)


def test_on_exit_can_schedule(scheduler):
    mech = DummyMechanism("Mechanism", scheduler)
    main = mech.run(Coroutine.park).named("Main Mech Command")
    background = mech.run(Coroutine.park).named("Background Mech Command")
    nxt = park("Next")
    signal = Ref(False)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(main), sm.add_state(nxt)
    sm.set_initial_state(s1)
    s1.switch_to(s2).when(signal.get)
    s1.on_exit(lambda: scheduler.schedule(background))
    scheduler.schedule(sm)
    scheduler.run()
    assert scheduler.is_running(sm) and scheduler.is_running(main)
    signal.set(True)
    scheduler.run()
    assert scheduler.is_running(sm)
    assert not scheduler.is_running(main)
    assert scheduler.is_running(background)
    assert scheduler.is_running(nxt)


def test_runs_on_enter_callbacks_in_insertion_order(scheduler):
    info = []
    sm = StateMachine("State Machine")
    s1 = sm.add_state(Command.no_requirements(lambda co: None).named("Command 1"))
    sm.set_initial_state(s1)
    s1.on_enter(lambda: info.append("onEnter 1"))
    s1.on_enter(lambda: info.append("onEnter 2"))
    scheduler.schedule(sm)
    scheduler.run()
    assert info == ["onEnter 1", "onEnter 2"]


def test_runs_on_exit_callbacks_in_insertion_order(scheduler):
    info = []
    sm = StateMachine("State Machine")
    s1 = sm.add_state(Command.no_requirements(lambda co: None).named("Command 1"))
    sm.set_initial_state(s1)
    s1.on_exit(lambda: info.append("onExit 1"))
    s1.on_exit(lambda: info.append("onExit 2"))
    scheduler.schedule(sm)
    scheduler.run()
    assert info == ["onExit 1", "onExit 2"]


def test_on_enter_sees_new_command(scheduler):
    c1 = park("Command 1")
    saw = Ref(False)
    sm = StateMachine("State Machine")
    s1 = sm.add_state(c1)
    sm.set_initial_state(s1)
    s1.on_enter(lambda: saw.set(scheduler.is_running(c1)))
    scheduler.schedule(sm)
    scheduler.run()
    assert saw.get()


def test_on_exit_with_transition_sees_exited_command(scheduler):
    c1, c2 = park("Command 1"), park("Command 2")
    saw, signal = Ref(False), Ref(False)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)
    s1.on_exit(lambda: saw.set(scheduler.is_running(c1)))
    s1.switch_to(s2).when(signal.get)
    scheduler.schedule(sm)
    scheduler.run()
    signal.set(True)
    scheduler.run()
    assert saw.get()


def test_on_exit_with_complete_cannot_see_exited_command(scheduler):
    c1, c2 = yield_once("Command 1"), park("Command 2")
    called, saw = Ref(False), Ref(False)
    sm = StateMachine("State Machine")
    s1, s2 = sm.add_state(c1), sm.add_state(c2)
    sm.set_initial_state(s1)

    def on_exit():
        called.set(True)
        saw.set(scheduler.is_running(c1))

    s1.on_exit(on_exit)
    s1.switch_to(s2).when_complete()
    scheduler.schedule(sm)
    scheduler.run()
    assert not called.get()
    scheduler.run()
    assert called.get()
    assert not saw.get()


def test_led_state_machine(scheduler):
    class Leds(DummyMechanism):
        def idle_animation(self):
            return self.run(Coroutine.park).with_priority(-1).named("Default Animation")

        def info_animation(self):
            return self.run(Coroutine.yield_).with_priority(0).named("Info")

        def warning_animation(self):
            return self.run(Coroutine.yield_).with_priority(1).named("Warning")

    leds = Leds("LEDs", scheduler)
    normal = Trigger(scheduler, lambda: True)
    high = Trigger(scheduler, lambda: True)
    sm = StateMachine("State Machine")
    idle = sm.add_state(leds.idle_animation())
    info = sm.add_state(leds.info_animation())
    warning = sm.add_state(leds.warning_animation())
    sm.set_initial_state(idle)
    idle.switch_to(info).when(normal & ~high)
    idle.switch_to(warning).when(high)
    warning.switch_to(info).when_complete_and(normal)
    info.switch_to(warning).when_complete_and(high)
    sm.switch_from_any().to(warning).when(high)
    sm.switch_from_any().to(idle).when_complete()
