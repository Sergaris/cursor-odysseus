"""Windows: скрытые subprocess и headless launch Cursor SDK bridge."""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_SUBPROCESS_PATCHED = False
_BRIDGE_PATCHED = False


def resolve_hidden_bridge_argv(bridge_path: Path | str | None = None) -> list[str] | None:
    """Вместо .cmd возвращает [node.exe, cursor-sdk-bridge.js] без окна cmd.

    Args:
        bridge_path: Путь к cursor-sdk-bridge.cmd или None для resolve_bridge_path().

    Returns:
        argv для AsyncBridge.launch или None, если headless-режим недоступен.
    """
    if sys.platform != "win32":
        return None

    if bridge_path is None:
        try:
            from cursor_sdk._vendor import resolve_bridge_path

            bridge_path = Path(resolve_bridge_path())
        except ImportError:
            return None
    else:
        bridge_path = Path(bridge_path)

    if bridge_path.suffix.lower() not in {".cmd", ".bat"}:
        return None

    bin_dir = bridge_path.parent
    node = bin_dir / "node.exe"
    bridge_js = bin_dir.parent / "dist" / "bin" / "cursor-sdk-bridge.js"
    if node.is_file() and bridge_js.is_file():
        return [str(node), str(bridge_js)]

    logger.warning(
        "Headless bridge: node.exe или bridge.js не найдены рядом с %s",
        bridge_path,
    )
    return None


def install_subprocess_hide_console() -> None:
    """Патчит asyncio.create_subprocess_* с CREATE_NO_WINDOW на Windows."""
    global _SUBPROCESS_PATCHED
    if sys.platform != "win32" or _SUBPROCESS_PATCHED:
        return
    _SUBPROCESS_PATCHED = True

    orig_exec = asyncio.create_subprocess_exec
    orig_shell = asyncio.create_subprocess_shell

    async def exec_no_window(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return await orig_exec(*args, **kwargs)

    async def shell_no_window(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return await orig_shell(*args, **kwargs)

    asyncio.create_subprocess_exec = exec_no_window  # type: ignore[assignment]
    asyncio.create_subprocess_shell = shell_no_window  # type: ignore[assignment]
    logger.debug("asyncio subprocess patched with CREATE_NO_WINDOW")


def _rewrite_bridge_command(command):
    """Подменяет .cmd launcher на прямой вызов node + bridge.js."""
    if command is None:
        return resolve_hidden_bridge_argv()
    if isinstance(command, (str, os.PathLike)):
        return resolve_hidden_bridge_argv(os.fspath(command)) or command
    return command


def install_hidden_bridge_launch() -> None:
    """Monkey-patch cursor_sdk Bridge.launch для headless node.exe на Windows."""
    global _BRIDGE_PATCHED
    if sys.platform != "win32" or _BRIDGE_PATCHED:
        return

    try:
        import cursor_sdk._async_bridge as async_bridge
        import cursor_sdk._bridge as sync_bridge
    except ImportError:
        return

    _BRIDGE_PATCHED = True
    orig_async_launch = async_bridge.AsyncBridge.launch

    async def hidden_async_launch(cls, command=None, **kwargs):
        rewritten = _rewrite_bridge_command(command)
        return await orig_async_launch(rewritten, **kwargs)

    async_bridge.AsyncBridge.launch = classmethod(hidden_async_launch)

    orig_sync_launch = sync_bridge.Bridge.launch

    def hidden_sync_launch(cls, command=None, **kwargs):
        rewritten = _rewrite_bridge_command(command)
        return orig_sync_launch(rewritten, **kwargs)

    sync_bridge.Bridge.launch = classmethod(hidden_sync_launch)
    logger.debug("Cursor SDK bridge launch patched for headless node.exe")


def configure_windows_subprocess_runtime() -> None:
    """Один раз на старте: скрытые subprocess и headless bridge."""
    if sys.platform != "win32":
        return
    install_subprocess_hide_console()
    install_hidden_bridge_launch()
