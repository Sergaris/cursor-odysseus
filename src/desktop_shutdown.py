"""Graceful shutdown for the Odysseus desktop shell (Windows)."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 15.0
CURSOR_SDK_SHUTDOWN_TIMEOUT_SEC = 12.0
_shutdown_lock = threading.Lock()
_shutdown_done = False

# Подпроцессы Odysseus, которые могут пережить родителя (bridge node, MCP worker).
_ORPHAN_CMD_MARKERS = (
    "cursor_sdk/_vendor/bridge",
    "cursor_sdk\\_vendor\\bridge",
    "mcp_servers/",
    "mcp_servers\\",
)


class UvicornServerController:
    """Runs uvicorn in a background thread and supports cooperative stop."""

    def __init__(self, app: Any, *, host: str, port: int) -> None:
        import uvicorn

        self._config = uvicorn.Config(app, host=host, port=port, log_level="info")
        self._server = uvicorn.Server(self._config)
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the FastAPI server on a daemon thread."""
        if self.is_running:
            return
        self._thread = threading.Thread(
            target=self._server.run,
            daemon=True,
            name="odysseus-server",
        )
        self._thread.start()

    def stop(self, *, timeout_sec: float = GRACEFUL_SHUTDOWN_TIMEOUT_SEC) -> None:
        """Request uvicorn exit and wait for lifespan shutdown hooks."""
        if getattr(self._server, "started", False):
            self._server.should_exit = True
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.1, timeout_sec))
            if thread.is_alive():
                logger.warning(
                    "Uvicorn не завершился за %.1f с — будут завершены дочерние процессы",
                    timeout_sec,
                )


def is_shutdown_done() -> bool:
    """True, если shutdown уже выполнялся."""
    return _shutdown_done


def get_process_cleanup_markers() -> tuple[str, ...]:
    """Return path markers used to identify Odysseus-owned child processes."""
    markers: list[str] = []
    if getattr(sys, "frozen", False):
        markers.append(os.path.dirname(os.path.abspath(sys.executable)))
    from src.runtime_paths import get_app_root

    markers.append(get_app_root())
    normalized: list[str] = []
    for marker in markers:
        if not marker:
            continue
        cleaned = os.path.normcase(os.path.abspath(marker))
        if cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)


def _list_win32_processes() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
    except Exception as exc:
        logger.warning("Не удалось получить список процессов Windows: %s", exc)
    return []


def _is_descendant(pid: int, ancestor: int, parent_map: dict[int, int]) -> bool:
    seen: set[int] = set()
    current = pid
    while current and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        current = parent_map.get(current, 0)
    return False


def _cmd_matches_install(cmd: str, marker_cases: tuple[str, ...]) -> bool:
    return bool(cmd) and any(marker in cmd for marker in marker_cases)


def _cmd_is_orphan_child(cmd: str) -> bool:
    return any(marker in cmd for marker in _ORPHAN_CMD_MARKERS)


def collect_straggler_pids(root_pid: int, markers: tuple[str, ...]) -> set[int]:
    """Find Odysseus-owned child/orphan processes for this install."""
    if sys.platform != "win32" or not markers:
        return set()
    processes = _list_win32_processes()
    if not processes:
        return set()
    parent_map = {
        int(item.get("ProcessId", 0)): int(item.get("ParentProcessId", 0) or 0)
        for item in processes
        if item.get("ProcessId")
    }
    marker_cases = tuple(os.path.normcase(m) for m in markers)
    stragglers: set[int] = set()
    for item in processes:
        pid = int(item.get("ProcessId", 0) or 0)
        if pid in {0, root_pid}:
            continue
        cmd = os.path.normcase(str(item.get("CommandLine") or ""))
        is_child = _is_descendant(pid, root_pid, parent_map)
        if is_child:
            # Любой потомок (WebView2, MCP worker, bridge node, дочерний exe).
            stragglers.add(pid)
            continue
        if _cmd_matches_install(cmd, marker_cases) and _cmd_is_orphan_child(cmd):
            stragglers.add(pid)
    return stragglers


def _terminate_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _shutdown_cursor_sdk_sync() -> None:
    """Закрывает Cursor SDK bridge в отдельном asyncio loop (не блокируется uvicorn)."""
    from src.cursor_sdk.async_runtime import shutdown_async_clients

    errors: list[BaseException] = []

    def _run() -> None:
        try:
            asyncio.run(shutdown_async_clients())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(
        target=_run,
        name="cursor-sdk-shutdown",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=CURSOR_SDK_SHUTDOWN_TIMEOUT_SEC)
    if thread.is_alive():
        logger.warning("Cursor SDK shutdown не завершился за %.1f с", CURSOR_SDK_SHUTDOWN_TIMEOUT_SEC)
    if errors:
        logger.warning("Cursor SDK shutdown error: %s", errors[0])


def _release_desktop_instance_lock() -> None:
    try:
        from src.desktop_single_instance import release_primary_instance

        release_primary_instance()
    except Exception as exc:
        logger.debug("Instance lock release skipped: %s", exc)


def cleanup_straggler_processes(root_pid: int | None = None) -> int:
    """Force-kill remaining Odysseus child processes. Returns count killed."""
    root = root_pid or os.getpid()
    pids = collect_straggler_pids(root, get_process_cleanup_markers())
    for proc_id in sorted(pids, reverse=True):
        _terminate_pid(proc_id)
    if pids:
        logger.info("Завершено фоновых процессов Odysseus: %s", len(pids))
    return len(pids)


def shutdown_odysseus_desktop(
    server: UvicornServerController | None,
    *,
    stop_tray: Callable[[], None] | None = None,
) -> None:
    """Gracefully stop backend, then guarantee child process cleanup."""
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            logger.debug("Повторный shutdown проигнорирован")
            return
        _shutdown_done = True
        logger.info("Завершение Odysseus desktop...")

    root_pid = os.getpid()

    if stop_tray is not None:
        try:
            stop_tray()
        except Exception:
            logger.exception("Не удалось остановить иконку в трее")

    if server is not None:
        try:
            server.stop()
        except Exception:
            logger.exception("Ошибка остановки uvicorn")

    _shutdown_cursor_sdk_sync()
    cleanup_straggler_processes(root_pid)
    _release_desktop_instance_lock()
    os._exit(0)
