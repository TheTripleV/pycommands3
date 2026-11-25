"""Port of SchedulerTelemetryTests.java (decoded with Google's protobuf runtime)."""

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from commands3._common.api import Command
from commands3._common.telemetry import SCHEDULER_TYPE, file_descriptor
from commands3._frontends.asyncawait import Coroutine

from .helpers import DummyMechanism

EPSILON = 1e-9


@pytest.fixture(scope="module")
def scheduler_message_class():
    fdp = descriptor_pb2.FileDescriptorProto.FromString(file_descriptor())
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(SCHEDULER_TYPE))


def test_protobuf(scheduler, scheduler_message_class):
    mech = DummyMechanism("The mechanism", scheduler)
    park = mech.run(Coroutine.park).named("Park")

    async def c3_body(co: Coroutine):
        await co.await_(park)

    c3 = mech.run(c3_body).named("C3")

    async def c2_body(co: Coroutine):
        await co.await_(c3)

    c2 = mech.run(c2_body).named("C2")

    async def group_body(co: Coroutine):
        await co.await_(c2)

    group = mech.run(group_body).named("Group")
    scheduler.schedule(group)
    scheduler.run()
    s1 = Command.no_requirements(Coroutine.park).named("Command 1")
    s2 = Command.no_requirements(Coroutine.park).named("Command 2")
    scheduler.schedule(s1)
    scheduler.schedule(s2)

    message = scheduler_message_class.FromString(scheduler.to_protobuf())
    assert abs(message.last_time_ms - scheduler.last_runtime_ms()) < EPSILON
    assert len(message.queued_commands) == 2
    assert len(message.running_commands) == 4

    def check(msg, command, parent, *requirements):
        assert msg.id == scheduler.run_id(command)
        assert msg.name == command.name()
        assert msg.priority == command.priority()
        assert [r.name for r in msg.requirements] == list(requirements)
        if parent is None:
            assert not msg.HasField("parent_id")
        else:
            assert msg.parent_id == scheduler.run_id(parent)
        if scheduler.is_running(command):
            assert msg.HasField("last_time_ms") and msg.HasField("total_time_ms")
            assert abs(msg.last_time_ms - scheduler.last_command_runtime_ms(command)) < EPSILON
            assert abs(msg.total_time_ms - scheduler.total_runtime_ms(command)) < EPSILON
        else:
            assert not msg.HasField("last_time_ms") and not msg.HasField("total_time_ms")

    check(message.queued_commands[0], s1, None)
    check(message.queued_commands[1], s2, None)
    check(message.running_commands[0], group, None, "The mechanism")
    check(message.running_commands[1], c2, group, "The mechanism")
    check(message.running_commands[2], c3, c2, "The mechanism")
    check(message.running_commands[3], park, c3, "The mechanism")

    snap = scheduler.snapshot()
    assert [c["name"] for c in snap["running_commands"]] == ["Group", "C2", "C3", "Park"]
