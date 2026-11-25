"""Features without a Java test to port: Trigger.debounce and NT telemetry publishing."""

import ntcore
import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

import commands3.custom.asyncawait as v
from commands3._common.telemetry import SCHEDULER_TYPE, SchedulerPublisher, file_descriptor


@pytest.fixture
def sched():
    s = v.Scheduler.create_independent_scheduler()
    yield s
    s.close()


@pytest.mark.parametrize("dtype,expect", [
    (v.DebounceType.RISING, [False, False, True, True, False]),  # high for >= 0.1 s
    (v.DebounceType.FALLING, [True, True, True, True, True]),
])
def test_debounce(sched, clock, dtype, expect):
    raw = {"v": False}
    t = v.Trigger(sched, lambda: raw["v"]).debounce(0.1, dtype)
    sched.run()
    seen = []
    # high for 0.15 s (long enough for RISING), then a short low blip of 0.02 s
    for dt, value in ((0.0, True), (0.05, True), (0.05, True), (0.05, True), (0.02, False)):
        clock.advance(dt)
        raw["v"] = value
        sched.run()
        seen.append(t())
    assert seen == expect


def test_scheduler_publisher_round_trip(sched):
    inst = ntcore.NetworkTableInstance.create()
    try:
        pub = SchedulerPublisher(sched, "/Commands/Scheduler", instance=inst)
        sched.schedule(v.Command.no_requirements(v.Coroutine.park).named("Parked"))
        sched.run()
        sub = inst.get_raw_topic("/Commands/Scheduler").subscribe(f"proto:{SCHEDULER_TYPE}", b"")
        pub.update()
        raw = sub.get()
        assert inst.has_schema("proto:protobuf_commands.proto")
        pool = descriptor_pool.DescriptorPool()
        pool.Add(descriptor_pb2.FileDescriptorProto.FromString(file_descriptor()))
        msg = message_factory.GetMessageClass(pool.FindMessageTypeByName(SCHEDULER_TYPE)).FromString(raw)
        assert [c.name for c in msg.running_commands] == ["Parked"]
    finally:
        ntcore.NetworkTableInstance.destroy(inst)
