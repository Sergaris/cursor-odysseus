"""Async session registry manager для chat/research scopes."""

import asyncio
import logging

from src.cursor_sdk.async_session_registry import AsyncSessionRegistry
from src.cursor_sdk.provider import normalize_cursor_sdk_model

logger = logging.getLogger(__name__)

_REGISTRY_LOCK = asyncio.Lock()
_REGISTRIES: dict[tuple[str, str, str], AsyncSessionRegistry] = {}


class AsyncSessionRegistryManager:
    """Process-wide async registries keyed by (model, cwd, api_key)."""

    @staticmethod
    async def get_registry(*, api_key: str, model: str, cwd: str) -> AsyncSessionRegistry:
        model = normalize_cursor_sdk_model(model)
        key = (model, cwd, api_key)
        async with _REGISTRY_LOCK:
            registry = _REGISTRIES.get(key)
            if registry is None:
                registry = AsyncSessionRegistry(api_key=api_key, model=model, cwd=cwd)
                _REGISTRIES[key] = registry
                logger.info("Cursor SDK async registry создан: model=%s cwd=%s", model, cwd)
            return registry

    @staticmethod
    async def release_chat(session_id: str) -> None:
        if not session_id:
            return
        async with _REGISTRY_LOCK:
            registries = list(_REGISTRIES.values())
        for registry in registries:
            await registry.release("chat", session_id)

    @staticmethod
    async def reset_for_tests() -> None:
        async with _REGISTRY_LOCK:
            registries = list(_REGISTRIES.values())
            _REGISTRIES.clear()
        for registry in registries:
            await registry.release_all()
