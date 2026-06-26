"""Live integration tests for Cursor SDK (real API key required).

Runs isolated subprocess to avoid Windows asyncio/bridge conflicts in pytest.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.cursor_sdk

_RUNNER = Path(__file__).resolve().parent / "cursor_sdk_live_runner.py"


def _require_api_key() -> None:
    from src.cursor_sdk.auth import CursorSDKAuthError, resolve_api_key

    try:
        resolve_api_key(None)
    except CursorSDKAuthError as exc:
        pytest.skip(str(exc))


def test_live_cursor_sdk_subprocess_suite():
    """Full live suite in isolated subprocess with real Cursor API."""
    _require_api_key()
    proc = subprocess.run(
        [sys.executable, str(_RUNNER)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=600,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        pytest.fail(
            f"live runner failed (code={proc.returncode})\n"
            f"stderr:\n{proc.stderr[-4000:]}"
        )

    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"invalid runner output: {exc}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )

    failures = {name: status for name, status in results.items() if not str(status).startswith("OK")}
    if failures:
        pytest.fail("live cursor-sdk failures:\n" + json.dumps(failures, ensure_ascii=False, indent=2))

    assert len(results) >= 6
