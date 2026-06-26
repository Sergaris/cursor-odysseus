"""Python runtime helpers for agent tools."""

import os
from pathlib import Path

from src.python_runtime import (
    PYTHON_WORKER_ENV,
    prepare_python_code,
    python_subprocess_env,
    write_python_script,
)


def test_prepare_python_code_adds_matplotlib_preamble():
    code = prepare_python_code("import matplotlib.pyplot as plt\nplt.plot([1])")
    assert "MPLBACKEND" in code
    assert "matplotlib.use" in code


def test_prepare_python_code_skips_when_backend_set():
    code = 'os.environ["MPLBACKEND"] = "Agg"\nimport matplotlib.pyplot as plt'
    assert prepare_python_code(code) == code


def test_python_subprocess_env_sets_worker_flag():
    env = python_subprocess_env({})
    assert env[PYTHON_WORKER_ENV] == "1"
    assert env["MPLBACKEND"] == "Agg"


def test_write_python_script_in_cwd(tmp_path: Path):
    script = write_python_script("print(42)", cwd=str(tmp_path))
    try:
        assert script.is_file()
        text = script.read_text(encoding="utf-8")
        assert "print(42)" in text
        assert script.parent == tmp_path
    finally:
        script.unlink(missing_ok=True)
