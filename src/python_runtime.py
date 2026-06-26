"""Запуск Python-кода для agent tools (dev + frozen portable exe)."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

_MATPLOTLIB_PREAMBLE = """\
import os
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg", force=True)
except Exception:
    pass
"""

_PYTHON_WORKER_ENV = "ODYSSEUS_PYTHON_WORKER"
PYTHON_WORKER_ENV = _PYTHON_WORKER_ENV


def resolve_python_executable() -> str:
    """Интерпретатор для subprocess python tool."""
    return sys.executable or "python"


def is_frozen_python_worker() -> bool:
    """True, если frozen exe запущен как python worker (не GUI)."""
    return bool(getattr(sys, "frozen", False)) and os.environ.get(_PYTHON_WORKER_ENV) == "1"


def prepare_python_code(raw_code: str) -> str:
    """Добавляет headless matplotlib preamble при необходимости."""
    code = (raw_code or "").strip()
    if not code:
        return code
    lowered = code.lower()
    if "matplotlib" in lowered or "plt." in code or "pyplot" in lowered:
        if "MPLBACKEND" not in code and "matplotlib.use" not in code:
            return _MATPLOTLIB_PREAMBLE + "\n" + code
    return code


def write_python_script(code: str, *, cwd: str) -> Path:
    """Пишет временный .py в workspace агента и возвращает путь."""
    root = Path(cwd)
    root.mkdir(parents=True, exist_ok=True)
    script_path = root / f".odysseus_run_{uuid.uuid4().hex}.py"
    script_path.write_text(prepare_python_code(code), encoding="utf-8")
    return script_path


def python_subprocess_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Env для python subprocess с headless matplotlib."""
    env = dict(base_env or os.environ)
    env[_PYTHON_WORKER_ENV] = "1"
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def cleanup_python_script(path: Path | None) -> None:
    """Удаляет временный script после выполнения."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
