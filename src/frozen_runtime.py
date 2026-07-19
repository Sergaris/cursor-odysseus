"""Bootstrap для portable-сборки PyInstaller (Windows exe)."""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_BRIDGE_LAUNCHER = "cursor-sdk-bridge.cmd" if sys.platform == "win32" else "cursor-sdk-bridge"
_BRIDGE_BIN_ENV = "CURSOR_SDK_BRIDGE_BIN"


def is_frozen() -> bool:
    """True, если процесс запущен из PyInstaller-сборки."""
    return bool(getattr(sys, "frozen", False))


def resolve_bundled_bridge_path() -> Path | None:
    """Ищет cursor-sdk-bridge в распакованном bundle PyInstaller.

    Returns:
        Абсолютный путь к launcher или None, если bridge не найден.
    """
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(
            Path(meipass) / "cursor_sdk" / "_vendor" / "bridge" / "bin" / _BRIDGE_LAUNCHER
        )

    try:
        import cursor_sdk._vendor as vendor
    except ImportError:
        vendor = None
    if vendor is not None:
        candidates.append(
            Path(vendor.__file__).resolve().parent / "bridge" / "bin" / _BRIDGE_LAUNCHER
        )

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def configure_frozen_runtime() -> None:
    """Один раз на старте exe: bridge path и рабочая директория агента."""
    if not is_frozen():
        return

    if not os.environ.get(_BRIDGE_BIN_ENV, "").strip():
        bridge = resolve_bundled_bridge_path()
        if bridge is not None:
            os.environ[_BRIDGE_BIN_ENV] = str(bridge)
            logger.info("Cursor SDK bridge: %s", bridge)
        else:
            logger.warning(
                "Cursor SDK bridge не найден в bundle. "
                "Переустановите portable-сборку или задайте %s.",
                _BRIDGE_BIN_ENV,
            )

    from src.runtime_paths import ensure_default_workspace_dir

    from src.windows_subprocess import configure_windows_subprocess_runtime

    configure_windows_subprocess_runtime()
    ensure_default_workspace_dir()

    from src.bundled_searxng import schedule_bundled_searxng_background

    schedule_bundled_searxng_background()
