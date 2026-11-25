"""Port of SchedulerTest.java."""

from commands3._common.api import Command, Mechanism
from commands3._frontends.asyncawait import Coroutine, co

from .helpers import DummyMechanism, Ref


def test_basic(scheduler):
    enabled = Ref(False)
    ran = Ref(False)

    async def body():
        while True:
            await co.yield_()
            if enabled.get():
                break
        ran.set(True)

    command = Command.no_requirements(body).named("Basic Command")
    scheduler.schedule(command)
    scheduler.run()
    assert scheduler.is_running(command), "Command should be running after being scheduled"
    enabled.set(True)
    scheduler.run()
    assert not scheduler.is_running(command), \
        "Command should no longer be running after awaiting its completion"
    assert ran.get()


def test_atomicity(scheduler):
    mechanism = DummyMechanism("X", scheduler)
    mechanism.x = 0
    num_commands = 100
    iterations = 500

    def make(i):
        async def body():
            for _ in range(iterations):
                mechanism.x += 1
                await co.yield_()

        return Command.no_requirements(body).named(f"CountCommand[{i}]")

    for i in range(num_commands):
        scheduler.schedule(make(i))
    for _ in range(iterations):
        scheduler.run()
    assert mechanism.x == num_commands * iterations


def test_run_mechanism(scheduler):
    example = DummyMechanism("Counting", scheduler)
    example.x = 0

    async def body():
        example.x = 0
        for _ in range(10):
            await co.yield_()
            example.x += 1

    count_to_ten = example.run(body).named("Count To Ten")
    scheduler.schedule(count_to_ten)
    for _ in range(10):
        scheduler.run()
    scheduler.run()
    assert example.x == 10


def test_compositions_do_not_need_requirements(scheduler):
    m1 = DummyMechanism("M1", scheduler)
    m2 = DummyMechanism("m2", scheduler)

    async def body():
        await co.await_all(
            m1.run(co.park).named("M1 Command"),
            m2.run(co.park).named("M2 Command"),
        )

    group = Command.no_requirements(body).named("Composition")
    scheduler.schedule(group)
    scheduler.run()
    assert len(scheduler.get_running_commands()) == 3


def test_nested_mechanisms(scheduler):
    class Superstructure(DummyMechanism):
        def __init__(self):
            super().__init__("Superstructure", scheduler)
            self.elevator = DummyMechanism("Elevator", scheduler)
            self.arm = DummyMechanism("Arm", scheduler)
            self.arm.set_default_command(self.arm.idle())
            self.elevator.set_default_command(self.elevator.idle())
            self.set_default_command(self.idle())

        def super_command(self) -> Command:
            async def body():
                await co.await_(self.elevator.run(co.park).named("Elevator Subcommand"))
                await co.await_(self.arm.run(co.park).named("Arm Subcommand"))

            return self.run(body).named("Super Command")

    superstructure = Superstructure()
    scheduler.schedule(superstructure.super_command())
    scheduler.run()
    assert superstructure.arm.get_running_commands() == [superstructure.arm.get_default_command()], \
        "Arm should only be running its default command"

    scheduler.schedule(superstructure.elevator.run(co.park).named("Conflict"))
    scheduler.run()  # schedules the default superstructure command
    scheduler.run()  # starts running the default superstructure command
    assert superstructure.get_running_commands() == [superstructure.get_default_command()]
