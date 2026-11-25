"""Benchmarks for all eight variants.

  PYTHONPATH=. .venv/bin/python bench/bench.py

Scenarios are written once in async/await and translated per style with tools/translate.py.
"""

import gc
import importlib
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from translate import translate  # noqa: E402

from commands3._common import robot_state  # noqa: E402

robot_state.set_fetcher(robot_state.MockRobotStateFetcher())

SCENARIOS = '''
from commands3._common.api import Command, Mechanism


async def busy(co):
    while True:
        await co.yield_()


async def parked(co):
    await co.wait_until(lambda: False)


def nest(depth):
    async def body(co):
        if depth == 0:
            while True:
                await co.yield_()
        await co.await_(Command.no_requirements(nest(depth - 1)).named(f"n{depth}"))
    return body


def chain(depth, log):
    async def leaf(co):
        log.append("leaf-start")
        await co.yield_()
        log.append("leaf-end")

    def level(n):
        async def body(co):
            await co.await_(Command.no_requirements(leaf if n == 1 else level(n - 1)).named(f"L{n}"))
            log.append(f"resumed-{n}")
        return body
    return level(depth)


def mixed(scheduler):
    """A robot-ish mix: 6 mechanisms with default commands, a state-machine-like parent
    re-forking children every few cycles, plus parked and busy commands."""
    mechs = [Mechanism(f"m{i}", scheduler) for i in range(6)]
    for m in mechs:
        m.set_default_command(m.run_repeatedly(lambda: None).named(f"{m.get_name()}.default"))

    async def supervisor(co):
        k = 0
        while True:
            k += 1
            m = mechs[k % 6]
            co.fork(m.run(busy).named(f"{m.get_name()}.job"))
            for _ in range(3):
                await co.yield_()

    scheduler.schedule(Command.no_requirements(supervisor).named("supervisor"))
    for i in range(10):
        scheduler.schedule(Command.no_requirements(parked).named(f"p{i}"))
'''

STYLES = ["asyncawait", "generators", "asyncgen", "greenlets"]
RUNTIMES = ["custom", "aio"]


def load(style):
    src = SCENARIOS if style == "asyncawait" else translate(SCENARIOS, style)
    ns = {}
    exec(compile(src, f"<bench-{style}>", "exec"), ns)
    return ns


def tick_cost(variant, ns, scenario, ticks=1500):
    s = variant.Scheduler.create_independent_scheduler()
    C = ns["Command"]
    if scenario == "50 busy":
        for i in range(50):
            s.schedule(C.no_requirements(ns["busy"]).named(f"c{i}"))
    elif scenario == "50 parked":
        for i in range(50):
            s.schedule(C.no_requirements(ns["parked"]).named(f"c{i}"))
    elif scenario == "10 x depth-5":
        for i in range(10):
            s.schedule(C.no_requirements(ns["nest"](5)).named(f"r{i}"))
    elif scenario == "robot-ish mix":
        ns["mixed"](s)
    for _ in range(5):
        s.run()
    gc.collect()
    t = time.perf_counter()
    for _ in range(ticks):
        s.run()
    return (time.perf_counter() - t) / ticks * 1e6


def nested_latency(variant, ns, depth=5):
    s = variant.Scheduler.create_independent_scheduler()
    log, ticks = [], {}
    s.schedule(ns["Command"].no_requirements(ns["chain"](depth, log)).named("root"))
    for t in range(10):
        before = len(log)
        s.run()
        for e in log[before:]:
            ticks.setdefault(e, t)
    return ticks["leaf-start"], ticks[f"resumed-{depth}"] - ticks["leaf-end"]


def mem_per_command(variant, ns, n=2000):
    s = variant.Scheduler.create_independent_scheduler()
    C = ns["Command"]
    cmds = [C.no_requirements(ns["busy"]).named(f"c{i}") for i in range(n)]
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.take_snapshot()
    for c in cmds:
        s.schedule(c)
    s.run()
    snap = tracemalloc.take_snapshot()
    tracemalloc.stop()
    return sum(st.size_diff for st in snap.compare_to(base, "filename")) / n


def main():
    scenarios = ["50 busy", "50 parked", "10 x depth-5", "robot-ish mix"]
    rows = []
    for runtime in RUNTIMES:
        for style in STYLES:
            v = importlib.import_module(f"commands3.{runtime}.{style}")
            ns = load(style)
            costs = [min(tick_cost(v, ns, sc) for _ in range(3)) for sc in scenarios]
            start_lat, resume_lat = nested_latency(v, ns)
            rows.append({"variant": f"{runtime}.{style}",
                         **{sc: round(c, 1) for sc, c in zip(scenarios, costs)},
                         "depth-5 leaf start (cycles)": start_lat,
                         "depth-5 root resume delay (cycles)": resume_lat,
                         "heap bytes/command": round(mem_per_command(v, ns))})
    print(f"Python {platform.python_version()} on {platform.machine()} ({platform.system()})")
    print("Scheduler.run() cost in microseconds per cycle (lower is better)\n")
    keys = list(rows[0])
    widths = [max(len(k), *(len(str(r[k])) for r in rows)) for k in keys]
    print(" | ".join(k.ljust(w) for k, w in zip(keys, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(r[k]).ljust(w) for k, w in zip(keys, widths)))
    (ROOT / "bench" / "results.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
