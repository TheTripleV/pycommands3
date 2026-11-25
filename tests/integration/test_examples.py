"""Integration: every example robot x every variant, in real robotpy simulation.

Each run is a subprocess (HAL sim state and the examples' top-level module names are
process-global). Runs execute in parallel; results are checked per example, and the logical
traces must be identical across all eight variants.
"""

import concurrent.futures as cf
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
EXAMPLES = ["template", "skeleton", "hatchbot", "rebuilt", "superstructure"]
STYLES = ["asyncawait", "generators", "asyncgen", "greenlets"]
RUNTIMES = ["custom", "aio"]
VARIANTS = list(itertools.product(STYLES, RUNTIMES))


def _run(example, style, runtime):
    proc = subprocess.run(
        [sys.executable, str(HERE / "run_robot.py"), example, style, runtime],
        capture_output=True, text=True, timeout=180, cwd=HERE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise AssertionError(f"{example}/{style}/{runtime} failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")


@pytest.fixture(scope="module")
def results():
    jobs = {(e, s, r): None for e in EXAMPLES for s, r in VARIANTS}
    with cf.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        futs = {pool.submit(_run, *k): k for k in jobs}
        for f in cf.as_completed(futs):
            jobs[futs[f]] = f.result()
    return jobs


@pytest.mark.parametrize("example", EXAMPLES)
def test_traces_identical_across_variants(results, example):
    base = results[(example, "asyncawait", "custom")]
    for style, runtime in VARIANTS:
        r = results[(example, style, runtime)]
        assert r["scheduler_class"] == f"commands3.{runtime}.{style}"
        assert r["trace"] == base["trace"], f"{style}/{runtime} trace differs"
        assert r["facts"] == base["facts"], f"{style}/{runtime} facts differ"


def test_template(results):
    f = results[("template", "asyncawait", "custom")]["facts"]
    assert f["teleop_runs"] == 5
    assert f["after_release"] == []
    assert f["auto_with_stick_pressed"] == [], "teleop binding must be scoped to its opmode"


def test_hatchbot(results):
    f = results[("hatchbot", "asyncawait", "custom")]["facts"]
    assert f["arcade_left"] > 0
    assert f["after_grab"].endswith("FORWARD") and f["after_release"].endswith("REVERSE")
    assert f["half_speed_left"] == pytest.approx(f["arcade_left"] / 2, abs=0.01)
    assert 60 <= f["auto_distance"] < 62
    assert "Drive Distance[60@0.5]" not in f["auto_running_at_end"]
    assert "Arcade Drive" in f["auto_running_at_end"], "default command resumes after auto"


def test_rebuilt(results):
    f = results[("rebuilt", "asyncawait", "custom")]["facts"]
    assert {"Intake.Intake", "Intake.Roller.Intake"} <= set(f["intake"])
    assert f["intake_wrist"][0] == "POSITION" and f["intake_wrist"][1] < 0
    assert "Intake.Idle" in f["after_agitate"]
    assert {"Sweep and Score", "Intake.Agitate", "Shooter.ShootAtHub"} <= set(f["auto"])
    wrist = results[("rebuilt", "asyncawait", "custom")]["trace"]
    targets = {round(t[1], 3) for t in wrist}
    assert {0.175, -0.175} <= targets, "agitate should alternate wrist targets"


def test_superstructure_cascade(results):
    t = [tuple(x) for x in results[("superstructure", "asyncawait", "custom")]["facts"]["transitions"]]
    assert t[:3] == [(0, "Stow", "HOLDING"), (5, "Intake", "IDLE"), (22, "Stow", "HOLDING")]
    assert (45, "PrepScore", "MOVING") in t
    assert (52, "Stow", "MOVING") in t, "releasing score mid-way backs out in the same cycle"
    assert any(name == "Intake.eject" for _, name, _ in t)
    trace = results[("superstructure", "asyncawait", "custom")]["trace"]
    row45 = next(r for r in trace if r[0] == 45)
    assert row45[3] == 12.0, "superstructure -> state -> elevator SM -> motor, all in one cycle"
