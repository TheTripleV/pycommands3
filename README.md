# pycommands3: WPILib Commands v3 for Python, sixteen ways

This is a complete Python port of `allwpilib/commandsv3` (2027 beta), implemented once per
combination of **coroutine style** × **runtime** × **`co` convention** (explicit `co` parameter
vs. global `co` with no parameter):

| | custom runtime (`commands3.custom.*`) | asyncio runtime (`commands3.aio.*`) |
|---|---|---|
| async/await | `asyncawait` / `asyncawait_noco` | `asyncawait` / `asyncawait_noco` |
| generators | `generators` / `generators_noco` | `generators` / `generators_noco` |
| async generators | `asyncgen` / `asyncgen_noco` | `asyncgen` / `asyncgen_noco` |
| greenlet | `greenlets` / `greenlets_noco` | `greenlets` / `greenlets_noco` |

It targets robotpy `wpilib==2027.0.0a8` (snake_case API, OpModeRobot) and Python 3.13+.

## The same command in each style

```python
# async/await                                    # generators
async def body(co: Coroutine):                   def body(co: Coroutine):
    while not elevator.at_goal():                    while not elevator.at_goal():
        elevator.drive_toward_goal()                     elevator.drive_toward_goal()
        await co.yield_()                                yield
    await co.await_(gripper.release())               yield from co.await_(gripper.release())

# async generators                               # greenlet
async def body(co: Coroutine):                   def body(co: Coroutine):
    while not elevator.at_goal():                    while not elevator.at_goal():
        elevator.drive_toward_goal()                     elevator.drive_toward_goal()
        yield                    # one cycle             co.yield_()   # from any call depth
    await co.await_(gripper.release())               co.await_(gripper.release())

score = elevator.run(body).named("Score")        # identical in every style
```

### Without the `co` parameter (`*_noco`)

The `*_noco` variants take no `co` parameter (like `commandsv2`). Command bodies take no
arguments, and suspend through the global `co` proxy, which refers to the command that is
currently running, so `co` is only valid inside a command body. Import it from the variant
module for the style you use:

```python
# async/await (commands3.custom.asyncawait_noco)     # generators (commands3.custom.generators_noco)
async def body():                                   def body():
    while not elevator.at_goal():                       while not elevator.at_goal():
        elevator.drive_toward_goal()                        elevator.drive_toward_goal()
        await co.yield_()                                   yield
    await co.await_(gripper.release())                  yield from co.await_(gripper.release())

# async generators (commands3.custom.asyncgen_noco)   # greenlet (commands3.custom.greenlets_noco)
async def body():                                   def body():
    while not elevator.at_goal():                       while not elevator.at_goal():
        elevator.drive_toward_goal()                        elevator.drive_toward_goal()
        yield                    # one cycle              co.yield_()   # from any call depth
    await co.await_(gripper.release())                  co.await_(gripper.release())

score = elevator.run(body).named("Score")           # identical in every style
```

A complete command, from `examples/hatchbot/asyncawait_noco/autos.py`. The only change from
the `co`-parameter version is that `body` takes no argument:

```python
from commands3.custom.asyncawait_noco import Command, co

def complex_auto(drive: DriveMechanism, hatch: HatchMechanism) -> Command:
    async def body():
        await co.await_(drive.drive_distance(AutoConstants.AUTO_DRIVE_DISTANCE_INCHES,
                                             AutoConstants.AUTO_DRIVE_SPEED))
        await co.await_(hatch.release_hatch_command())
        await co.await_(drive.drive_distance(AutoConstants.AUTO_BACKUP_DISTANCE_INCHES,
                                             AutoConstants.AUTO_DRIVE_SPEED))

    return Command.requiring(drive, hatch).executing(body).named("Complex Auto")
```

The `*_noco` variants run on the same scheduler as the `co`-parameter variants, so the
robot class looks the same. Only the import changes. Using the asyncio runtime, import from
`commands3.aio.*_noco` instead of `commands3.custom.*_noco`:

```python
from commands3.aio.asyncawait_noco import Command, Scheduler, co   # asyncio runtime
from commands3.button import CommandXboxController

class Robot(wpilib.OpModeRobot):
    def robot_periodic(self):
        Scheduler.get_default().run()
```

```python
from commands3.custom.asyncawait import Command, Coroutine, Mechanism, Scheduler, StateMachine, Trigger
from commands3.button import CommandXboxController

class Robot(wpilib.OpModeRobot):
    def robot_periodic(self):
        Scheduler.get_default().run()
```

The whole Java API is here in snake_case: `Scheduler` (priorities, default commands, binding
scopes for opmodes, robot modes and commands, sideloads, `add_periodic`, events, telemetry),
`Coroutine` (`yield_`, `park`, `fork`, `await_`, `await_all`, `await_any`, `wait`,
`wait_until`, `request_cancellation`, `ForkResult`), staged builders, `SequentialGroup` and
`ParallelGroup` with their builders, `StateMachine`, `Trigger` (all bindings, composition,
debounce, edges, `multi_press`), `Mechanism`, `CommandRunTracker`, `ConflictDetector`,
`SysIdRoutine`, and every button/controller class (the 12 controllers are generated from
WPILib's `first_ds_hids.json`). Durations are float seconds and volts are floats, following
robotpy convention.

## How it's built

The variants share everything that is specification, and differ exactly where the
approaches differ:

```
commands3/_common/      the spec, written once: Command, builders, groups, StateMachine,
                        Mechanism, Trigger, binding scopes, results/events, telemetry, and
                        the scheduling algorithm itself (port of Scheduler.java)
commands3/_frontends/   how user code suspends: Coroutine API + task adapter per style
commands3/_runtime/     who resumes it: custom (direct stepping) | asyncio (Task per command)
commands3/{custom,aio}/ the sixteen public variant modules (eight per runtime)
```

Every suspension resolves to a `Park` object, the Python equivalent of Java's
`CoroutineState`. Framework-internal commands (groups, state machines, waits, idle, sysid)
are written once as Park-yielding generators and run under every variant.

The **asyncio runtime** runs each command run as a real `asyncio.Task`, named after the
command:
- **Resume order:** drivers are woken one at a time in Java's order (children before
  parents), so ordering is deterministic and identical to the custom runtime.
- **Forks:** forked children are created with `eager_start=True`, so there are no dead
  cycles at any nesting depth.
- **Foreign awaitables:** user code can `await asyncio.to_thread(...)`, `asyncio.sleep(...)`,
  or any asyncio future. These resolve at cycle granularity because the loop only runs
  inside `Scheduler.run()`.
- **Selector:** a custom selector polls the OS once per cycle instead of on every loop
  iteration; that change made the runtime about 7× faster.

## Results

### Tests

| suite | what | passing |
|---|---|---|
| `tests/conformance` | All 23 Java test classes (the 274 Java test cases, a few merged via parametrization), written once in async/await (both `co`-param and `*_noco`) and mechanically translated to the other 3 styles (`tools/translate.py`), each run on both runtimes, plus code-review regression tests and extra scoped-trigger tests | **4504** |
| `tests/integration` | 5 robot projects (WPILib's commandv3 template and skeleton, `hatchbotcmdv3`, `rebuiltcmdv3`, and a cascading-superstructure demo) × 16 variants, run under the real robot framework in robotpy simulation. Traces must be identical across variants | **9** (80 robot runs) |
| `tests/test_frontends.py` | Style-specific behaviour and failure modes (both `co`-param and `*_noco`) | 44 |
| `tests/test_asyncio_interop.py` | `to_thread`, `asyncio.sleep`, foreign futures, cancellation, named tasks | 7 |
| `tests/test_buttons.py` | Controllers and buttons against `DriverStationSim` | 15 |
| `tests/test_static_analysis.py` | What pyright catches per style | 1 |
| `tests/test_extras.py` | Debounce, NetworkTables protobuf telemetry | 3 |
| total | | **4583**, about 14 s |

`pyright commands3` reports 0 errors, and every example in every style also type-checks
with 0 errors.

### Performance

From `bench/bench.py`: Python 3.13 on Apple Silicon, µs per `Scheduler.run()`. The benchmark
covers the eight `co`-parameter variants only. The `*_noco` variants use the same frontends
and runtimes, differing only in how `co` is passed.

| variant | 50 busy | 50 parked | 10 × depth-5 | robot-ish mix | dead cycles (start / resume) | heap B/command |
|---|---|---|---|---|---|---|
| custom.generators | **35** | 9 | **18** | **27** | 0 / 0 | 1436 |
| custom.asyncgen | 47 | 9 | 21 | 29 | 0 / 0 | 1460 |
| custom.asyncawait | 50 | 9 | 24 | 30 | 0 / 0 | 1828 |
| custom.greenlets | 83 | 9 | 28 | 38 | 0 / 0 | 4508 |
| aio.generators | 207 | 33 | 75 | 80 | 0 / 0 | 2295 |
| aio.asyncawait | 213 | 30 | 75 | 81 | 0 / 0 | 2755 |
| aio.asyncgen | 223 | 32 | 77 | 77 | 0 / 0 | 2465 |
| aio.greenlets | 251 | 31 | 82 | 86 | 0 / 0 | 5596 |

### What each style catches, and where it bites

| | generators | async/await | async generators | greenlet |
|---|---|---|---|---|
| Forgotten `yield from` / `await` on a framework op | runtime warning (ours) | **pyright error + runtime warning** | **pyright error + runtime warning** | n/a |
| …on a *user* helper | **silently does nothing** | **pyright error + runtime warning** | **pyright error + runtime warning** | n/a |
| Helpers that suspend | `yield from helper(co)` | `await helper(co)` | helpers must be plain `async def`; a helper with a bare `yield` becomes an async generator and can't be awaited | plain call, any depth |
| Suspension visible at call site | yes | yes | yes | **no** (can suspend inside `sorted(key=...)`) |
| `finally` runs on cancel | yes | yes | yes | yes |
| Cleanup that suspends | error | error | error (detected: CPython silently allows it) | error |
| Can return values | yes | yes | no (`return x` is a SyntaxError) | yes |
| Dependencies | none | none | none | greenlet (C ext; arm64 wheels exist) |

## Deviations from Java (all deliberate)

- **`finally` runs on cancellation.** A canceled command's body is unwound with
  `finally` blocks run, innermost first; Java just abandons the continuation. Cleanup code
  must not suspend.
- **Fork failure and `request_cancellation()` are exceptions.** They are raised as
  internal `BaseException`s rather than a "yield forever" state, so `fork()` never suspends
  and `except Exception` in user code can't swallow them.
- **Cancellation on the call stack unwinds immediately.** If a command is canceled while
  it is on the call stack (for example, a child cancels its composition root), its frames
  unwind right away (`Unwind`). In Java the parent keeps running until its next yield.
- **`on_exit` runs when a command's body raises.** Java skips it on that path, even
  though `on_exit` is documented to run whenever the command stops for any reason.
- **Cancellation always finishes.** If cleanup raises while a command is being canceled
  (its `finally`, `on_exit` or `on_cancel`), the remaining hooks, the `Canceled` event and the
  cancellation of its children still happen; the first error is then re-raised.
- **`cancel_all()` called inside a command stops that command's body** right there, like
  `cancel()` does.
- **A stale binding only cancels the run it started.** Java keeps every binding until its
  scope goes stale, so a command scheduled in teleop and again in auto was canceled by the
  stale teleop binding, and the binding list grew with every schedule. Here each binding
  is dropped when its run ends.
- **Error traces use exception notes.** Errors carry the binding/schedule site as an
  exception note (`=== Command Binding Trace ===`) instead of rewritten stack frames.
- **`Scheduler.close()` exists.** It releases the asyncio runtime's event loop.
- **Not ported:** Epilogue logging (`@Logged`) and the Java compiler plugin's checks
  (`@NoDiscard`, etc.) have no Python equivalent. Pyright's `reportUnusedCoroutine` covers
  the most important one for the async styles.

`tasks.md` has the full work log and the robotpy 2027 alpha quirks found along the way.

## Commands

```sh
uv venv -p 3.13 .venv && uv pip install -p .venv --prerelease=allow -e ".[test]"
.venv/bin/python -m pytest                      # everything (~6 s)
.venv/bin/python bench/bench.py                 # benchmarks for the 8 co-parameter variants
.venv/bin/pyright commands3                     # type check
./tools/regen.sh                                # re-translate tests + examples from async/await
tools/java_parity/run.sh                        # run the scoped-trigger scenarios on Java commandsv3 (JDK 25)
.venv/bin/python tools/gen_controllers.py       # regenerate controller classes
```

To run an example robot: `cd examples/superstructure/asyncawait && ../../../.venv/bin/python -m robotpy sim`
(GUI simulator). Booting with robotpy's own starter has been checked headless (`robotpy run`).
