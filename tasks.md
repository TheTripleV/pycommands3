# pycommands3 — Python ports of WPILib Commands v3

Goal: implement all of `allwpilib/commandsv3` in Python, several times: 4 coroutine styles
(generators, async generators, greenlet, async/await) × 2 runtimes (custom scheduler, asyncio),
with the full feature set, the Java test suite ported, example projects, and integration tests.

Reference: `../allwpilib/commandsv3` (2027 beta), `../allwpilib/design-docs/commands-v3*.md`,
examples in `../allwpilib/wpilibjExamples/src/main/java/org/wpilib/{examples/*cmdv3,templates/commandv3*}`.
Target: robotpy `wpilib==2027.0.0a8` (snake_case API, nanosecond `RobotController.get_time`).

## Architecture decisions

- **One spec layer, eight variants.** Everything that is not "how a command suspends" or "who
  resumes it" is shared (`commands3/_common`): Command, builders, groups, StateMachine, Mechanism,
  Trigger, binding scopes, events, ScheduleResult, ConflictDetector, CommandRunTracker, telemetry,
  sysid, buttons. The *scheduling algorithm* (port of `Scheduler.java`) is also shared, with two
  hooks for mounting/closing a command. Copy-pasting 8 schedulers would only make the comparison
  noisier; the variants differ exactly where the approaches differ.
- **Park protocol** (= Java `CoroutineState`): every suspension in every variant resolves to a
  `Park` object (LIVE, FROZEN, ParkedUntil, ParkedOnCondition, ParkedWhile). Framework-internal
  commands (groups, StateMachine, wait commands, idle) are written once as Park-yielding generators.
- **Front-ends** (`commands3/_frontends`): Coroutine API + adapter that turns a user body into a
  steppable task. generators: `yield` / `yield from co.wait(1)`; async generators: bare `yield`
  = one tick, `await co.wait(1)`; async/await: `await co.yield_()`; greenlet: `co.yield_()`.
- **Runtimes** (`commands3/_runtime`): `custom` steps tasks directly; `aio` runs each command run
  as a real `asyncio.Task`, resumed via futures in Java's deterministic order, eager-started for
  same-cycle forks, and can await foreign asyncio awaitables (to_thread, sleep...).
- **Public modules**: `commands3.custom.{generators,asyncgen,asyncawait,greenlets}` and
  `commands3.aio.{...}`; each exports the full API (Scheduler, Command, Mechanism, Trigger,
  Coroutine, StateMachine, ...). `commands3.button`, `commands3.sysid` shared.
- **Python-isms**: snake_case (matches robotpy 2027); durations are float seconds; `yield_`/`await_`
  (keywords); `ForkFailed` cancellation is an internal BaseException so user `except Exception`
  can't swallow it; `finally` blocks run on cancellation (Java can't do this).
- **Tests**: Java tests ported to pytest in canonical async/await style (`tests/conformance/async`),
  mechanically translated into the other 3 styles by `tools/translate.py` (checked in, regenerated
  by `tools/regen.sh`), each run against both runtimes via a fixture.

## Tasks

Legend: [ ] todo, [~] in progress, [x] done, [-] skipped (with reason)

### 0. Setup
- [x] Clone allwpilib, robotpy-commands-v2 (+PR #12); read v3 sources, tests, design docs
- [x] venv with wpilib 2027.0.0a8, greenlet, pytest, pyright
- [x] pyproject, package skeleton, tools/translate.py, tools/regen.sh

### 1. Shared spec layer (`commands3/_common`)
- [x] time source (RobotController.get_time, overridable), RobotStateFetcher (+ mock)
- [x] Park states (CoroutineState), CommandRunTracker
- [x] BindingScope (Global/ForCommand/ForOpmode/Auto/Teleop/Utility), Binding, BindingType
- [x] SchedulerEvent types, ScheduleResult types, ConflictDetector, trace helper (add_note)
- [x] Command base + decorators (with_timeout, until, and_then, along_with, race_with, ...)
- [x] StagedCommandBuilder / NeedsExecution / NeedsName stages
- [x] SequentialGroup(+Builder), ParallelGroup(+Builder), wait_until/wait_for
- [x] StateMachine (+ transitions builder stages, callbacks, switch_from_any)
- [x] Mechanism (run, run_repeatedly, idle, idle_for, default command, running commands)
- [x] Trigger (all bindings, and/or/negate, debounce, edges, multi_press, scopes) + Debouncer
- [x] Scheduler core (schedule/cancel/run/priorities/ancestry/defaults/sideload/add_periodic/
      cancel_all/disabled handling/events/telemetry getters/stale bindings/trace injection)

### 2. Runtimes
- [x] custom runtime
- [x] asyncio runtime (Task per command run, deterministic resume, eager fork, foreign awaitables)

### 3. Front-ends
- [x] async/await
- [x] generators (+ unconsumed-op warning)
- [x] async generators
- [x] greenlet

### 4. Public variant modules
- [x] commands3.custom.{asyncawait,generators,asyncgen,greenlets}
- [x] commands3.aio.{asyncawait,generators,asyncgen,greenlets}

### 5. Extras
- [x] telemetry: protobuf encoding (dependency-free) + FileDescriptor, NT publishing helper
- [x] sysid: SysIdRoutine (uses wpilib.sysid.SysIdRoutineLog)
- [x] buttons: CommandGenericHID, CommandGamepad, CommandJoystick, InternalButton,
      JoystickButton, POVButton, NetworkButton, RobotModeTriggers
- [x] generated controllers (Xbox, PS4, PS5, ... ) via tools/gen_controllers.py

### 6. Conformance tests (port of the 274 Java tests) — canonical async, translated ×3, ×2 runtimes
Status: 2116 passing (all 8 variants), 9 s. Python additions: finally-on-cancel, suspending sideloads.
- [x] SchedulerTest (5)              - [x] CoroutineTest (18)
- [x] SchedulerCancellationTests (43) - [x] SchedulerPriorityLevelTests (13)
- [x] SchedulerDefaultCommandTests (6) - [x] SchedulerConflictTests (5)
- [x] SchedulerErrorHandlingTests (5) - [x] SchedulerSideloadFunctionTests (8)
- [x] SchedulerDisabledTests (8)     - [x] SchedulerTimingTests (7)
- [x] SchedulerTelemetryTests (1)    - [x] CommandRunTrackerTest (12)
- [x] BindingScopeTest (4)           - [x] ConflictDetectorTest (4)
- [x] SequentialGroupTest (4)        - [x] SequentialGroupBuilderTest (12)
- [x] ParallelGroupTest (8)          - [x] ParallelGroupBuilderTest (9)
- [x] StagedCommandBuilderTest (27)  - [x] StateMachineTest (33)
- [x] TriggerTest (39)               - [x] SysIdRoutineTest (3)
- [-] SchedulerIsLoggableWithEpilogue (Java Epilogue annotation processor; no Python equivalent)
- [-] Java compiler-plugin checks (@NoDiscard, @PostConstructionInitializer, ...): no Python
      equivalent; pyright reportUnusedCoroutine covers forgotten awaits for the async styles

### 7. Front-end / runtime specific tests
- [x] footguns per style (forgotten await / yield from, yield in callbacks, cleanup can't suspend)
- [x] asyncio interop (to_thread, foreign futures, asyncio.sleep at tick granularity)
- [x] pyright check of style-specific misuse

### 8. Examples (each in 4 styles, runnable on both runtimes)
- [x] templates/commandv3 + commandv3skeleton
- [x] hatchbotcmdv3
- [x] rebuiltcmdv3
- [x] cascading superstructure example (superstructure SM → subsystem SMs)

### 9. Integration tests
- [x] run each example robot in robotpy simulation (DriverStationSim + step_timing) for every
      variant: enable, switch opmodes, press buttons, assert mechanism behaviour
- [x] cross-variant trace equality for the same robot

### 10. Wrap-up
- [x] benchmarks (tick cost, nested latency, memory) for all 8 variants
- [x] README: how to pick/import a variant, comparison results, deviations from Java
- [x] pyright clean on package

## Final status (2026-11-25)
- 2172 tests passing in ~6 s: 2116 conformance (all Java test classes x 8 variants), 40 robot
  sim runs (5 examples x 8 variants, identical traces), front-end/interop/button/extras tests.
- pyright: 0 errors on commands3 and on every example in every style.
- Benchmarks in bench/results.json (see README). Asyncio runtime was 7x slower until the
  once-per-cycle selector; custom runtime got 2-3x faster after removing per-mount
  RobotController.get_time() calls and event allocations when nobody listens.

## Notes / deviations log
- Fork failure / request_cancellation are internal BaseExceptions (not "yield forever"); fork() never suspends.
- Canceling a command that is on the call stack (e.g. a child cancels its composition root) unwinds the
  canceled frames immediately (`Unwind`), where Java lets the parent keep running until its next yield.
- Canceled bodies are closed: `finally` blocks run (Java just abandons the continuation). Cleanup code
  must not suspend (Python raises "ignored GeneratorExit").
- `cancel(None)` is a no-op (Java parity) — guard was needed because orphan removal keys on parent.
- Async-generator bodies: CPython lets a `yield` inside `finally` silently suspend during
  `asend().close()`; the front-end detects it and raises like the other styles.
- robotpy does not expose RobotState.ObserveUserProgramStarting, so robot mode reads UNKNOWN
  outside a running robot; robot-mode triggers are covered by full-robot integration tests.
- Controllers are generated as static code (tools/gen_controllers.py) and validated against
  the installed robotpy enums; button indices are 0-based in 2027.
- Examples import `commands3.custom.<style>`; the integration runner aliases it to
  `commands3.aio.<style>` to run the same robot on the asyncio runtime.
- 2027 sim: opmode ids carry the robot mode in bits 56-57 but the control word stores it
  elsewhere, so the harness sets both DS opmode and robot mode. Opmode constructors run while
  disabled: schedule mechanism-requiring commands from `start()` or via triggers.
- robotpy 2027a8 `PWMMotorController.get_throttle()` doesn't round-trip; hatchbot records
  commanded outputs itself.
- Integration: 5 example robots x 8 variants = 40 real-sim runs (subprocesses, parallel), ~2 s.
- Perf: runtime stats use perf_counter_ns (Java uses RobotController time); events are only
  constructed when a listener is registered.
- asyncio: each resume is 2 loop iterations; asyncio polls the selector every iteration, so a
  `_TickSelector` does one real poll per Scheduler.run() (I/O and thread wakeups still work).
- Async-generator style: a helper containing `await co.yield_()` becomes an async generator
  (bare `yield`) and cannot be awaited; helpers must stay plain coroutines.
- Translator: a bare `yield` may only replace `await <own coroutine param>.yield_()`.

## Code review fixes (2026-11-25)
- [x] Dropped Python 3.12 support (requires-python >=3.13). On 3.12, canceling an async-generator
      command suspended in an `await` raised "asynchronous generator is already running".
- [x] `_cancel` / `cancel_all`: a raising cleanup (body `finally`, on_exit, on_cancel) no longer
      skips the other hooks, the Canceled event or the cancellation of children; the first error is
      re-raised. The asyncio runtime always stops the command's driver Task.
- [x] `cancel_all()` inside a command raises `Unwind`, as `cancel()` does, so the body stops and can't
      fork orphans.
- [x] `on_exit` runs when a body raises (deviation from Java).
- [x] Bindings are tracked per live run (`_active_bindings: dict[Command, Binding]`, dropped when the
      run ends): no stale-binding cancel of a newer run, and no unbounded growth (deviation from Java).
- [x] Greenlet front-end rejects `async def` and generator bodies with TypeError, like the other styles.
- [x] JoystickButton/POVButton unwrap Command* controllers down to the wpilib HID.
- [x] Button trigger caches are keyed by the EventLoop object, not `id(loop)`.
- [x] The shared CommandGenericHID cache lives on the scheduler, so schedulers can be garbage-collected.
- [x] Regression tests: tests/conformance/*/test_review_regressions.py (8 tests x 8 variants) and new
      cases in test_frontends.py and test_buttons.py. 2245 tests passing; pyright clean; benchmark unchanged.

## Scoped trigger tests (2026-11-25)
- [x] tests/conformance/*/test_trigger_scopes.py (9 tests x 8 variants): while_true bound in a command
      is canceled when the command is canceled or completes; command scope beats opmode scope (binding
      and trigger lifetime); command-scoped bindings end with the opmode that owns the command; direct
      opmode 1 -> 2 switch; command reused by a trigger in the next opmode keeps running; returning to an
      opmode doesn't revive its old triggers; a signal held across a mode change doesn't refire edge
      bindings on the same trigger (if_true still does), but a new trigger fires on its first poll.
- [x] Verified against Java: tools/java_parity/ScopeScenarios.java runs the same scenarios on the real
      Java commandsv3 (compiled from ../allwpilib against the cached WPILib jars, JDK 25): all 33 checks pass.
