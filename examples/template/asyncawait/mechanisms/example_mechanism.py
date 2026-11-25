from commands3.custom.asyncawait import Command, Coroutine, Mechanism, Trigger


class ExampleMechanism(Mechanism):
    def __init__(self):
        super().__init__()
        self.run_count = 0
        self.example_condition = Trigger(lambda: False)

    def example_command(self) -> Command:
        async def body(co: Coroutine):
            self.run_count = 0
            while True:
                self.run_count += 1
                print(f"Example command run #{self.run_count}")
                await co.yield_()

        return self.run(body).named("Example Command")
