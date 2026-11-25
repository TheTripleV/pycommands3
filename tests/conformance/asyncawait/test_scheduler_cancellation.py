"""Port of SchedulerCancellationTests.java."""

from commands3._common.api import Canceled, Command, SequentialGroup
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism, PriorityCommand, Ref, assert_event


def test_cancel_on_interrupt_does_not_resume(scheduler):
    count = Ref(0)
    mechanism = DummyMechanism("mechanism", scheduler)
    interrupter = (Command.requiring(mechanism).executing(lambda co: None)
                   .with_priority(2).named("Interrupter"))

    async def body(co: Coroutine):
        count.set(1)
        await co.yield_()
        count.set(2)

    canceled = Command.requiring(mechanism).executing(body).with_priority(1).named("Cancel By Default")
    scheduler.schedule(canceled)
    scheduler.run()
    scheduler.schedule(interrupter)
    scheduler.run()
    assert count.get() == 1


def test_default_command_resumes_after_interruption(scheduler):
    count = Ref(0)
    mechanism = DummyMechanism("mechanism", scheduler)

    async def body(co: Coroutine):
        while True:
            count.increment_and_get()
            await co.yield_()

    default_cmd = mechanism.run(body).with_priority(-1).named("Default Command")
    newer_cmd = mechanism.run(lambda co: None).named("Newer Command")
    mechanism.set_default_command(default_cmd)
    scheduler.run()
    assert scheduler.is_running(default_cmd), "Default command should be running"
    scheduler.schedule(newer_cmd)
    scheduler.run()
    assert not scheduler.is_running(default_cmd), "Default command should have been interrupted"
    assert count.get() == 1, "Default command should have run once"
    scheduler.run()
    assert scheduler.is_running(default_cmd), "Default command should have resumed"
    assert count.get() == 2


def test_cancels_evicts_on_deck(scheduler):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.schedule(command)
    scheduler.cancel(command)
    assert not scheduler.is_scheduled_or_running(command)


def test_cancel_running_emits_event(scheduler, events):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.schedule(command)
    scheduler.run()
    scheduler.cancel(command)
    assert_event(events, Canceled, lambda e: e.command is command, "Cancellation did not emit an event")


def test_cancel_queued_emits_event(scheduler, events):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.schedule(command)
    scheduler.cancel(command)
    assert_event(events, Canceled, lambda e: e.command is command, "Cancellation did not emit an event")


def test_cancel_unknown_does_not_emit_event(scheduler, events):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.cancel(command)
    assert events == []


def test_cancel_null_does_nothing(scheduler, events):
    scheduler.cancel(None)  # type: ignore[arg-type]
    assert events == []


def test_command_canceling_self(scheduler, events):
    ran_after_cancel = Ref(False)
    command_ref = Ref()

    async def body(co: Coroutine):
        co.scheduler().cancel(command_ref.get())
        ran_after_cancel.set(True)

    command = Command.no_requirements(body).named("Self-Canceling Command")
    command_ref.set(command)
    scheduler.schedule(command)
    scheduler.run()
    assert not ran_after_cancel.get(), "Command should have stopped after canceling itself"
    assert_event(events, Canceled, lambda e: e.command is command, "Command should have been canceled")
    assert not scheduler.is_scheduled_or_running(command)


def test_request_cancellation(scheduler, events):
    ran_after_cancel = Ref(False)

    async def body(co: Coroutine):
        co.request_cancellation()
        ran_after_cancel.set(True)

    command = Command.no_requirements(body).named("Self-Canceling Command")
    scheduler.schedule(command)
    scheduler.run()
    assert not ran_after_cancel.get()
    assert_event(events, Canceled, lambda e: e.command is command)
    assert not scheduler.is_scheduled_or_running(command)


def test_request_cancellation_bubbles_down(scheduler, events):
    child = PriorityCommand(0)

    async def body(co: Coroutine):
        co.fork(child)
        co.request_cancellation()

    command = Command.no_requirements(body).named("Self-Canceling Command")
    scheduler.schedule(command)
    scheduler.run()
    assert_event(events, Canceled, lambda e: e.command is command, "Command should have been canceled")
    assert_event(events, Canceled, lambda e: e.command is child, "Child should have been canceled")
    assert not scheduler.is_scheduled_or_running(child)


def test_request_cancellation_does_not_bubble_up(scheduler, events):
    self_canceller = Command.no_requirements(Coroutine.request_cancellation).named("Self-Canceling Command")

    async def body(co: Coroutine):
        await co.await_(self_canceller)
        await co.park()

    parent = Command.no_requirements(body).named("Parent")
    scheduler.schedule(parent)
    scheduler.run()
    assert_event(events, Canceled, lambda e: e.command is self_canceller)
    assert scheduler.get_running_commands() == [parent]


def test_request_cancellation_calls_on_cancel(scheduler):
    callback_ran = Ref(False)
    command = (Command.no_requirements(Coroutine.request_cancellation)
               .when_canceled(lambda: callback_ran.set(True)).named("Self-Cancelling Command"))
    scheduler.schedule(command)
    scheduler.run()
    assert callback_ran.get(), "OnCancel callback should have been called"


def test_request_cancellation_calls_on_exit(scheduler):
    callback_ran = Ref(False)
    command = (Command.no_requirements(Coroutine.request_cancellation)
               .when_exited(lambda: callback_ran.set(True)).named("Self-Cancelling Command"))
    scheduler.schedule(command)
    scheduler.run()
    assert callback_ran.get(), "OnExit callback should have been called"


def test_cancel_all_evicts_on_deck(scheduler):
    command = Command.no_requirements(Coroutine.park).named("Command")
    scheduler.schedule(command)
    scheduler.cancel_all()
    assert not scheduler.is_scheduled_or_running(command)


def test_cancel_all_cancels_all(scheduler):
    commands = [Command.no_requirements(Coroutine.yield_).named(f"Command {i}") for i in range(1, 11)]
    for c in commands:
        scheduler.schedule(c)
    scheduler.run()
    scheduler.cancel_all()
    for c in commands:
        assert not scheduler.is_running(c), f"{c.name()} was not canceled by cancel_all()"


def test_cancel_all_calls_on_cancel_hook_for_running_commands(scheduler):
    ran = Ref(False)
    command = Command.no_requirements(Coroutine.park).when_canceled(lambda: ran.set(True)).named("Command")
    scheduler.schedule(command)
    scheduler.run()
    scheduler.cancel_all()
    assert ran.get(), "onCancel hook was not called"


def test_cancel_all_does_not_call_on_cancel_hook_for_queued_commands(scheduler):
    ran = Ref(False)
    command = Command.no_requirements(Coroutine.park).when_canceled(lambda: ran.set(True)).named("Command")
    scheduler.schedule(command)
    scheduler.cancel_all()
    assert not ran.get()


def test_cancel_all_calls_on_exit_hook_for_running_commands(scheduler):
    ran = Ref(False)
    command = Command.no_requirements(Coroutine.park).when_exited(lambda: ran.set(True)).named("Command")
    scheduler.schedule(command)
    scheduler.run()
    scheduler.cancel_all()
    assert ran.get(), "onExit hook was not called"


def test_cancel_all_does_not_call_on_exit_hook_for_queued_commands(scheduler):
    ran = Ref(False)
    command = Command.no_requirements(Coroutine.park).when_exited(lambda: ran.set(True)).named("Command")
    scheduler.schedule(command)
    scheduler.cancel_all()
    assert not ran.get(), "onExit hook was called when it shouldn't have been"


def test_cancel_all_starts_defaults(scheduler):
    mechanisms = []
    for i in range(1, 11):
        m = DummyMechanism(f"System {i}", scheduler)
        m.set_default_command(m.idle())
        mechanisms.append(m)
    command = Command.requiring(mechanisms).executing(Coroutine.yield_).named("Big Command")
    scheduler.schedule(command)
    scheduler.run()
    assert scheduler.is_running(command)
    for m in mechanisms:
        assert scheduler.get_running_commands_for(m) == [command]
    scheduler.cancel_all()
    assert not scheduler.is_running(command)
    scheduler.run()
    assert not scheduler.is_running(command)
    for m in mechanisms:
        running = scheduler.get_running_commands_for(m)
        assert len(running) == 1, f"mechanism {m} should have exactly one running command"
        assert running[0] is m.get_default_command()


def test_cancel_deeply_nested_compositions(scheduler):
    async def c3(co3: Coroutine):
        await co3.await_(Command.no_requirements(Coroutine.park).named("Park"))

    async def c2(co2: Coroutine):
        await co2.await_(Command.no_requirements(c3).named("C3"))

    async def root_body(co: Coroutine):
        await co.await_(Command.no_requirements(c2).named("C2"))

    root = Command.no_requirements(root_body).named("Root")
    scheduler.schedule(root)
    scheduler.run()
    assert len(scheduler.get_running_commands()) == 4
    scheduler.cancel(root)
    assert len(scheduler.get_running_commands()) == 0


def test_compositions_do_not_self_cancel(scheduler):
    mech = DummyMechanism("The mechanism", scheduler)

    async def c3(co3: Coroutine):
        await co3.await_(mech.run(Coroutine.park).named("Park"))

    async def c2(co2: Coroutine):
        await co2.await_(mech.run(c3).named("C3"))

    async def group_body(co: Coroutine):
        await co.await_(mech.run(c2).named("C2"))

    group = mech.run(group_body).named("Group")
    scheduler.schedule(group)
    scheduler.run()
    assert len(scheduler.get_running_commands()) == 4
    assert scheduler.is_running(group)


def test_compositions_do_not_cancel_parent(scheduler):
    mech = DummyMechanism("The mechanism", scheduler)

    async def body(co: Coroutine):
        co.fork(mech.run(Coroutine.park).named("First Child"))
        co.fork(mech.run(Coroutine.park).named("Second Child"))
        await co.park()

    group = mech.run(body).named("Group")
    scheduler.schedule(group)
    scheduler.run()
    assert [c.name() for c in scheduler.get_running_commands()] == ["Group", "Second Child"]


def test_compositions_with_shared_commands_do_not_self_cancel(scheduler):
    mech = DummyMechanism("Mech", scheduler)
    count, cancellation_count = Ref(0), Ref(0)

    async def inner(co: Coroutine):
        count.set(0)
        while True:
            count.increment_and_get()
            await co.yield_()

    inner_command = mech.run(inner).when_canceled(cancellation_count.increment_and_get).named("Inner Command")

    async def await_inner(c: Coroutine):
        await c.await_(inner_command)

    parent1 = Command.requiring(mech).executing(await_inner).named("Parent 1")
    parent2 = Command.requiring(mech).executing(await_inner).named("Parent 2")
    scheduler.schedule(parent1)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent1, inner_command]
    assert count.get() == 1
    scheduler.run()
    assert count.get() == 2
    scheduler.schedule(parent2)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent2, inner_command], "parent2 should have started"
    assert cancellation_count.get() == 1, "Inner command should have been canceled"
    assert count.get() == 1, "Inner command should have restarted"
    scheduler.run()
    assert scheduler.get_running_commands() == [parent2, inner_command]
    assert cancellation_count.get() == 1, "Inner command should not have been canceled again"
    assert count.get() == 2, "Inner command should have continued running"


def test_compositions_awaiting_same_command_do_not_interrupt(scheduler):
    mech = DummyMechanism("Mech", scheduler)
    shared = mech.run(Coroutine.park).named("Shared Command")

    async def await_shared(c: Coroutine):
        await c.await_(shared)

    parent1 = Command.no_requirements(await_shared).named("Parent 1")
    parent2 = Command.no_requirements(await_shared).named("Parent 2")
    scheduler.schedule(parent1)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent1, shared]
    scheduler.schedule(parent2)
    scheduler.run()
    assert scheduler.get_running_commands() == [parent1, shared, parent2]


def _hooked(mechanism, body, *, exit_ref=None, cancel_ref=None, name="cmd"):
    stage = mechanism.run(body)
    if exit_ref is not None:
        stage = stage.when_exited(lambda: exit_ref.set(True))
    if cancel_ref is not None:
        stage = stage.when_canceled(lambda: cancel_ref.set(True))
    return stage.named(name)


def test_does_not_run_on_cancel_when_interrupting_on_deck(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, cancel_ref=ran)
    interrupter = mechanism.run(Coroutine.yield_).named("Interrupter")
    scheduler.schedule(cmd)
    scheduler.schedule(interrupter)
    scheduler.run()
    assert not ran.get()


def test_does_not_run_on_cancel_when_canceling_on_deck(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, cancel_ref=ran)
    scheduler.schedule(cmd)
    scheduler.cancel(cmd)
    scheduler.run()
    assert not ran.get()


def test_runs_on_cancel_when_interrupting_command(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.park, cancel_ref=ran)
    interrupter = mechanism.run(Coroutine.park).named("Interrupter")
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.schedule(interrupter)
    scheduler.run()
    assert ran.get()


def test_does_not_run_on_cancel_when_completing(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, cancel_ref=ran)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.run()
    assert not scheduler.is_scheduled_or_running(cmd)
    assert not ran.get()


def test_runs_on_cancel_when_canceling(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, cancel_ref=ran)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.cancel(cmd)
    assert ran.get()


def test_runs_on_cancel_when_canceling_parent(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, cancel_ref=ran)
    group = SequentialGroup("Seq", [cmd])
    scheduler.schedule(group)
    scheduler.run()
    scheduler.cancel(group)
    assert ran.get()


def test_does_not_run_on_exit_when_interrupting_on_deck(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, exit_ref=ran)
    interrupter = mechanism.run(Coroutine.yield_).named("Interrupter")
    scheduler.schedule(cmd)
    scheduler.schedule(interrupter)
    scheduler.run()
    assert not ran.get()


def test_does_not_run_on_exit_when_canceling_on_deck(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, exit_ref=ran)
    scheduler.schedule(cmd)
    scheduler.cancel(cmd)
    scheduler.run()
    assert not ran.get()


def test_runs_on_exit_when_interrupting_command(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.park, exit_ref=ran)
    interrupter = mechanism.run(Coroutine.park).named("Interrupter")
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.schedule(interrupter)
    scheduler.run()
    assert ran.get()


def test_runs_on_exit_when_completing(scheduler):
    exit_ran, cancel_ran = Ref(False), Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, exit_ref=exit_ran, cancel_ref=cancel_ran)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.run()
    assert not scheduler.is_scheduled_or_running(cmd)
    assert exit_ran.get(), "onExit should have run on natural completion!"
    assert not cancel_ran.get(), "onCancel should not have run on natural completion!"


def test_runs_on_exit_when_canceling(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, exit_ref=ran)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.cancel(cmd)
    assert ran.get()


def test_runs_on_exit_when_canceling_parent(scheduler):
    ran = Ref(False)
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _hooked(mechanism, Coroutine.yield_, exit_ref=ran)
    group = SequentialGroup("Seq", [cmd])
    scheduler.schedule(group)
    scheduler.run()
    scheduler.cancel(group)
    assert ran.get()


def _ordered(stage, invocations):
    return (stage.when_exited(lambda: invocations.append("onExit"))
            .when_canceled(lambda: invocations.append("onCancel")).named("cmd"))


def test_on_exit_invoked_directly_before_on_cancel_when_canceling(scheduler):
    inv = []
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _ordered(mechanism.run(Coroutine.park), inv)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.cancel(cmd)
    assert inv == ["onExit", "onCancel"]


def test_on_exit_invoked_directly_before_on_cancel_when_requesting_cancellation(scheduler):
    inv = []
    cmd = _ordered(Command.no_requirements(Coroutine.request_cancellation), inv)
    scheduler.schedule(cmd)
    scheduler.run()
    assert inv == ["onExit", "onCancel"]


def test_on_exit_invoked_directly_before_on_cancel_when_interrupted(scheduler):
    inv = []
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _ordered(mechanism.run(Coroutine.park), inv)
    interrupter = mechanism.run(Coroutine.park).named("Interrupter")
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.schedule(interrupter)
    scheduler.run()
    assert inv == ["onExit", "onCancel"]


def test_on_exit_invoked_directly_before_on_cancel_when_cancel_all(scheduler):
    inv = []
    cmd = _ordered(Command.no_requirements(Coroutine.park), inv)
    scheduler.schedule(cmd)
    scheduler.run()
    scheduler.cancel_all()
    assert inv == ["onExit", "onCancel"]


def test_on_exit_invoked_directly_before_on_cancel_when_parent_canceled(scheduler):
    inv = []
    mechanism = DummyMechanism("The mechanism", scheduler)
    cmd = _ordered(mechanism.run(Coroutine.park), inv)
    group = SequentialGroup("Seq", [cmd])
    scheduler.schedule(group)
    scheduler.run()
    scheduler.cancel(group)
    assert inv == ["onExit", "onCancel"]


def test_custom_command_on_exit_invoked_before_on_cancel(scheduler):
    inv = []

    class Custom(Command):
        async def run(self, coroutine: Coroutine):
            await coroutine.park()

        def on_exit(self):
            inv.append("onExit")

        def on_cancel(self):
            inv.append("onCancel")

        def name(self):
            return "Custom"

        def requirements(self):
            return frozenset()

    custom = Custom()
    scheduler.schedule(custom)
    scheduler.run()
    scheduler.cancel(custom)
    assert inv == ["onExit", "onCancel"]


def test_finally_blocks_run_on_cancellation(scheduler):
    """Python addition: unlike Java, a canceled command's body unwinds (finally runs)."""
    order = []

    async def child(co: Coroutine):
        try:
            await co.park()
        finally:
            order.append("child")

    async def parent(co: Coroutine):
        try:
            await co.await_(Command.no_requirements(child).named("Child"))
        finally:
            order.append("parent")

    p = Command.no_requirements(parent).named("Parent")
    scheduler.schedule(p)
    scheduler.run()
    scheduler.cancel(p)
    assert sorted(order) == ["child", "parent"]
    assert scheduler.get_running_commands() == []
