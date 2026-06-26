"""Live discovery моделей Cursor SDK через async bridge (Windows-safe)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from src.cursor_sdk.auth import CursorSDKAuthError, resolve_api_key
from src.cursor_sdk.provider import resolve_cursor_sdk_cwd

logger = logging.getLogger(__name__)

_DISCOVERY_TIMEOUT_S = 45
_MAIN_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


def set_main_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Регистрирует uvicorn loop для sync probe из worker threads."""
    global _MAIN_EVENT_LOOP
    _MAIN_EVENT_LOOP = loop


def _run_on_loop(
    coro: Any,
    *,
    timeout: int,
) -> Any:
    """Выполняет coroutine на уже запущенном bridge loop (Windows-safe)."""
    loop: asyncio.AbstractEventLoop | None = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _MAIN_EVENT_LOOP

    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=max(10, int(timeout) + 5))
    return asyncio.run(coro)


async def list_cursor_sdk_models_async(
    *,
    api_key: str | None = None,
    base_url: str = "",
    timeout: int = _DISCOVERY_TIMEOUT_S,
) -> list[str]:
    """Возвращает id моделей из Cursor SDK (live list).

    Args:
        api_key: API key endpoint или None для env/settings.
        base_url: base_url endpoint (cwd из query).
        timeout: Таймаут в секундах.

    Returns:
        Отсортированный список model id.

    Raises:
        CursorSDKAuthError: Нет API key.
        RuntimeError: Bridge/list не ответил в срок или SDK недоступен.
    """
    from cursor_sdk.asyncio import AsyncCursor

    from src.cursor_sdk.async_runtime import get_async_client

    cwd = resolve_cursor_sdk_cwd(base_url)
    key = resolve_api_key(api_key)

    async def _fetch() -> list[str]:
        client = await get_async_client(cwd)
        models = await AsyncCursor.models.list(client=client, api_key=key)
        ids: list[str] = []
        for model in models:
            model_id = getattr(model, "id", None) or str(model)
            if isinstance(model_id, str) and model_id.strip():
                ids.append(model_id.strip())
        return sorted(set(ids))

    from src.cursor_sdk.async_runtime import retry_after_bridge_transport_error
    from src.cursor_sdk.provider import filter_cursor_sdk_model_ids

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            ids = await asyncio.wait_for(_fetch(), timeout=max(5, int(timeout or _DISCOVERY_TIMEOUT_S)))
            return filter_cursor_sdk_model_ids(ids)
        except Exception as exc:
            last_exc = exc
            action = await retry_after_bridge_transport_error(cwd, exc)
            if attempt == 0 and action != "no":
                if action == "relaunch":
                    logger.warning("Cursor SDK bridge stale during model list, relaunching")
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return []


def list_cursor_sdk_model_ids(
    *,
    api_key: str | None = None,
    base_url: str = "",
    timeout: int = _DISCOVERY_TIMEOUT_S,
) -> list[str]:
    """Sync-обёртка для probe routes (безопасна при вызове из sync FastAPI).

    Args:
        api_key: API key endpoint или None.
        base_url: base_url endpoint.
        timeout: Таймаут в секундах.

    Returns:
        Список model id или пустой список при ошибке auth/timeout.
    """
    try:
        return _run_on_loop(
            list_cursor_sdk_models_async(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            ),
            timeout=max(10, int(timeout or _DISCOVERY_TIMEOUT_S) + 5),
        )
    except CursorSDKAuthError:
        raise
    except concurrent.futures.TimeoutError as exc:
        logger.warning("Cursor SDK model discovery timed out")
        raise RuntimeError("Cursor SDK model discovery timed out") from exc
    except Exception as exc:
        logger.warning("Cursor SDK model discovery failed: %s", exc)
        raise RuntimeError(f"Cursor SDK model discovery failed: {exc}") from exc


def discovery_error_detail(exc: BaseException) -> str:
    """Короткое сообщение для UI/logs."""
    if isinstance(exc, CursorSDKAuthError):
        return str(exc)
    return str(exc)[:200]


def is_stale_cursor_sdk_cache(model_ids: list[str]) -> bool:
    """True, если cached_models похож на старый curated fallback."""
    if not model_ids:
        return True
    if len(model_ids) <= 5:
        return True
    legacy = {"composer-2.5-fast"}
    return any(mid in legacy for mid in model_ids)


async def refresh_cursor_sdk_endpoint_models() -> int:
    """Обновляет cached_models для всех cursor-sdk endpoints. Возвращает число обновлённых."""
    import json

    from core.database import ModelEndpoint, SessionLocal
    from src.cursor_sdk.provider import is_cursor_sdk_base

    db = SessionLocal()
    updated = 0
    try:
        endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        for ep in endpoints:
            base_url = (ep.base_url or "").strip()
            if not is_cursor_sdk_base(base_url):
                continue
            cached = []
            try:
                raw = ep.cached_models
                cached = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                cached = []
            if not isinstance(cached, list):
                cached = []
            if cached and not is_stale_cursor_sdk_cache(cached):
                continue
            try:
                models = await list_cursor_sdk_models_async(
                    api_key=ep.api_key,
                    base_url=base_url,
                )
            except Exception as exc:
                logger.warning(
                    "Cursor SDK startup model refresh failed for %s: %s",
                    getattr(ep, "id", "?"),
                    exc,
                )
                continue
            if not models:
                continue
            ep.cached_models = json.dumps(models)
            updated += 1
            logger.info(
                "Cursor SDK models refreshed: endpoint=%s count=%s",
                getattr(ep, "id", "?"),
                len(models),
            )
        if updated:
            db.commit()
    finally:
        db.close()
    return updated
