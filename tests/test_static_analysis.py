"""What a type checker catches for each style (pyright, default 'standard' settings)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PYRIGHT = shutil.which("pyright", path=str(Path(sys.executable).parent))

SNIPPETS = {
    "asyncawait": '''
from commands3.custom.asyncawait import Coroutine

async def helper(co: Coroutine) -> None:
    await co.wait(1.0)

async def body(co: Coroutine) -> None:
    helper(co)             # forgot await (user helper)
    co.wait(1.0)           # forgot await (framework op)
    await co.yield_()
''',
    "asyncgen": '''
from commands3.custom.asyncgen import Coroutine

async def helper(co: Coroutine) -> None:
    await co.wait(1.0)

async def body(co: Coroutine):
    helper(co)             # forgot await (user helper)
    co.wait(1.0)           # forgot await (framework op)
    yield
''',
    "generators": '''
from commands3.custom.generators import Coroutine

def helper(co: Coroutine):
    yield from co.wait(1.0)

def body(co: Coroutine):
    helper(co)             # forgot yield from (user helper)
    co.wait(1.0)           # forgot yield from (framework op)
    yield
''',
    "greenlets": '''
from commands3.custom.greenlets import Coroutine

def body(co: Coroutine) -> None:
    while True:
        pass               # forgot co.yield_(): freezes the robot; nothing can flag it
''',
    "asyncawait_noco": '''
from commands3.custom.asyncawait_noco import co

async def helper() -> None:
    await co.wait(1.0)

async def body() -> None:
    helper()               # forgot await (user helper)
    co.wait(1.0)           # forgot await (framework op)
    await co.yield_()
''',
    "asyncgen_noco": '''
from commands3.custom.asyncgen_noco import co

async def helper() -> None:
    await co.wait(1.0)

async def body():
    helper()               # forgot await (user helper)
    co.wait(1.0)           # forgot await (framework op)
    yield
''',
    "generators_noco": '''
from commands3.custom.generators_noco import co

def helper():
    yield from co.wait(1.0)

def body():
    helper()               # forgot yield from (user helper)
    co.wait(1.0)           # forgot yield from (framework op)
    yield
''',
    "greenlets_noco": '''
from commands3.custom.greenlets_noco import co

def body() -> None:
    while True:
        pass               # forgot co.yield_(): freezes the robot; nothing can flag it
''',
}

EXPECTED_FLAGGED = {
    "asyncawait": 2, "asyncgen": 2, "generators": 0, "greenlets": 0,
    "asyncawait_noco": 2, "asyncgen_noco": 2, "generators_noco": 0, "greenlets_noco": 0,
}


@pytest.mark.skipif(PYRIGHT is None, reason="pyright not installed")
def test_pyright_catches_forgotten_suspension(tmp_path):
    for style, src in SNIPPETS.items():
        (tmp_path / f"{style}.py").write_text(src)
    out = subprocess.run([PYRIGHT, "--outputjson", str(tmp_path)], capture_output=True,
                         text=True, cwd=Path(__file__).parent.parent)
    diags = json.loads(out.stdout)["generalDiagnostics"]
    per_style = {s: 0 for s in SNIPPETS}
    for d in diags:
        if d.get("rule") == "reportUnusedCoroutine":
            per_style[Path(d["file"]).stem] += 1
    assert per_style == EXPECTED_FLAGGED, diags
