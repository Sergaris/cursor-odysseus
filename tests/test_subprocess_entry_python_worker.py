"""Python worker argv detection for frozen exe."""

import os

from src.subprocess_entry import is_python_worker_argv, resolve_python_worker_script
from src.python_runtime import PYTHON_WORKER_ENV


def test_is_python_worker_argv_requires_env_and_script(tmp_path, monkeypatch):
    script = tmp_path / "run.py"
    script.write_text("print(1)", encoding="utf-8")
    monkeypatch.delenv(PYTHON_WORKER_ENV, raising=False)
    assert is_python_worker_argv(["exe", str(script)]) is False

    monkeypatch.setenv(PYTHON_WORKER_ENV, "1")
    assert is_python_worker_argv(["exe", str(script)]) is True
    assert resolve_python_worker_script(["exe", str(script)]) == str(script.resolve())


def test_is_python_worker_argv_rejects_missing_file(monkeypatch):
    monkeypatch.setenv(PYTHON_WORKER_ENV, "1")
    assert is_python_worker_argv(["exe", "missing.py"]) is False
