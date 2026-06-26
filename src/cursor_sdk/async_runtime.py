"""Async Cursor SDK bridge lifecycle (Windows-safe with asyncio)."""

import asyncio
import logging
import sys
import time
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from cursor_sdk.asyncio import AsyncClient

logger = logging.getLogger(__name__)

_BRIDGE_LAUNCH_ATTEMPTS = 3
_BRIDGE_LAUNCH_RETRY_DELAY_S = 0.35
_BRIDGE_DROP_COOLDOWN_S = 5.0

_CLIENTS: dict[str, "AsyncClient"] = {}
_CLIENT_LOCK = asyncio.Lock()
_last_bridge_drop_at = 0.0


def _ensure_windows_subprocess_runtime() -> None:
    if sys.platform != "win32":
        return
    from src.windows_subprocess import configure_windows_subprocess_runtime

    configure_windows_subprocess_runtime()


def is_bridge_transport_error(exc: BaseException) -> bool:
    """True, если ошибка похожа на мёртвый или недоступный bridge."""
    try:
        from cursor_sdk.errors import NetworkError

        if isinstance(exc, NetworkError):
            return True
    except ImportError:
        pass
    if type(exc).__name__ in {"ConnectError", "NetworkError", "ReadError"}:
        return True
    text = str(exc).lower()
    return (
        "connection attempts failed" in text
        or "connecterror" in text
        or "readerror" in text
        or "bridge request failed" in text
    )


def is_bridge_process_alive(cwd: str) -> bool:
    """True, если кэшированный bridge subprocess ещё работает."""
    from pathlib import Path

    workspace = str(Path(cwd).resolve())
    client = _CLIENTS.get(workspace)
    if client is None:
        return False
    owned = getattr(client, "_owned_bridge", None)
    if owned is None:
        return True
    proc = getattr(owned, "process", None)
    if proc is None:
        return True
    return proc.returncode is None


async def retry_after_bridge_transport_error(
    cwd: str,
    exc: BaseException,
) -> Literal["no", "retry", "relaunch"]:
    """Обрабатывает transport-ошибку: retry без relaunch или relaunch bridge.

    Returns:
        ``"retry"`` — повторить без relaunch (bridge жив).
        ``"relaunch"`` — bridge перезапущен, повторить операцию.
        ``"no"`` — не transport-ошибка.
    """
    if not is_bridge_transport_error(exc):
        return "no"
    if is_bridge_process_alive(cwd):
        logger.warning("Cursor SDK transient bridge error, retrying: %s", exc)
        return "retry"
    logger.warning("Cursor SDK bridge stale, relaunching: %s", exc)
    await drop_async_client(cwd)
    return "relaunch"


async def drop_async_client(cwd: str, *, force: bool = False) -> None:
    """Закрывает и удаляет кэшированный bridge client для workspace."""
    global _last_bridge_drop_at
    from pathlib import Path

    now = time.monotonic()
    if not force and (now - _last_bridge_drop_at) < _BRIDGE_DROP_COOLDOWN_S:
        logger.info(
            "Cursor SDK bridge drop skipped (cooldown %.1fs remaining)",
            _BRIDGE_DROP_COOLDOWN_S - (now - _last_bridge_drop_at),
        )
        return

    workspace = str(Path(cwd).resolve())
    async with _CLIENT_LOCK:
        client = _CLIENTS.pop(workspace, None)
    if client is None:
        return
    _last_bridge_drop_at = now
    try:
        await client.aclose()
    except Exception as exc:
        logger.warning("Cursor SDK client close failed during drop: %s", exc)


async def get_async_client(cwd: str) -> "AsyncClient":
    """Returns a shared AsyncClient with bridge for the workspace."""
    from pathlib import Path

    from cursor_sdk import LocalAgentOptions
    from cursor_sdk.asyncio import AsyncClient
    from cursor_sdk.errors import CursorSDKError

    _ensure_windows_subprocess_runtime()
    workspace = str(Path(cwd).resolve())
    async with _CLIENT_LOCK:
        client = _CLIENTS.get(workspace)
        if client is not None:
            return client

        last_exc: Exception | None = None
        for attempt in range(1, _BRIDGE_LAUNCH_ATTEMPTS + 1):
            try:
                client = await AsyncClient.launch_bridge(
                    workspace=workspace,
                    local=LocalAgentOptions(cwd=workspace, setting_sources=[]),
                )
                _CLIENTS[workspace] = client
                logger.info("Cursor SDK async bridge запущен: cwd=%s", workspace)
                return client
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Cursor SDK bridge launch attempt %s/%s failed: %s",
                    attempt,
                    _BRIDGE_LAUNCH_ATTEMPTS,
                    exc,
                )
                if attempt < _BRIDGE_LAUNCH_ATTEMPTS:
                    await asyncio.sleep(_BRIDGE_LAUNCH_RETRY_DELAY_S * attempt)

        msg = f"Cursor SDK bridge launch failed after {_BRIDGE_LAUNCH_ATTEMPTS} attempts"
        if isinstance(last_exc, CursorSDKError):
            raise CursorSDKError(f"{msg}: {last_exc}") from last_exc
        raise CursorSDKError(f"{msg}: {last_exc}") from last_exc


async def prewarm_async_client(cwd: str) -> None:
    """Best-effort bridge warmup so the first chat request does not pay cold-start."""
    try:
        await get_async_client(cwd)
    except Exception as exc:
        logger.warning("Cursor SDK bridge prewarm failed (non-critical): %s", exc)


async def shutdown_async_clients() -> None:
    """Закрывает все async bridge clients."""
    global _last_bridge_drop_at
    async with _CLIENT_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    _last_bridge_drop_at = 0.0
    for client in clients:
        try:
            await client.aclose()
        except Exception as exc:
            logger.warning("Cursor SDK async client close failed: %s", exc)


def reset_async_clients_for_tests() -> None:
    """Сбрасывает кэш clients без await — только если loop не держит bridge."""
    global _last_bridge_drop_at
    _CLIENTS.clear()
    _last_bridge_drop_at = 0.0
