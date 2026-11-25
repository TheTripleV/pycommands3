from commands3.custom.asyncawait import Command, Coroutine, Mechanism


class Intake(Mechanism):
    def __init__(self, plant):
        super().__init__("Intake")
        self.p = plant

    def until_piece(self) -> Command:
        async def body(co: Coroutine):
            self.p.roller_volts = 8.0
            try:
                await co.wait_until(lambda: self.p.has_piece)
            finally:
                self.p.roller_volts = 0.0

        return self.run(body).named("Intake.untilPiece")

    def eject(self) -> Command:
        async def body(co: Coroutine):
            self.p.roller_volts = -8.0
            try:
                await co.wait(0.3)
            finally:
                self.p.roller_volts = 0.0

        return self.run(body).named("Intake.eject")
