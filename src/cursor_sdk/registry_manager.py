"""Глобальный менеджер Cursor SDK session registry."""

import logging
import threading

from src.cursor_sdk.session_registry import SessionRegistry

logger = logging.getLogger(__name__)

_REGISTRY_LOCK = threading.Lock()
_CHAT_REGISTRIES: dict[tuple[str, str, str], SessionRegistry] = {}


class SessionRegistryManager:
    """Shared chat registries keyed by (model, cwd, api_key)."""

    @staticmethod
    def get_registry(*, api_key: str, model: str, cwd: str) -> SessionRegistry:
        """Returns a process-wide SessionRegistry for the credential tuple."""
        key = (model, cwd, api_key)
        with _REGISTRY_LOCK:
            registry = _CHAT_REGISTRIES.get(key)
            if registry is None:
                registry = SessionRegistry(api_key=api_key, model=model, cwd=cwd)
                _CHAT_REGISTRIES[key] = registry
                logger.info("Cursor SDK chat registry создан: model=%s cwd=%s", model, cwd)
            return registry

    @staticmethod
    def release_chat(session_id: str) -> None:
        """Закрывает chat-scoped agent для Odysseus session_id."""
        if not session_id:
            return
        with _REGISTRY_LOCK:
            registries = list(_CHAT_REGISTRIES.values())
        for registry in registries:
            registry.release("chat", session_id)

    @staticmethod
    def reset_for_tests() -> None:
        """Очищает все registries — только для тестов."""
        with _REGISTRY_LOCK:
            for registry in _CHAT_REGISTRIES.values():
                registry.release_all()
            _CHAT_REGISTRIES.clear()
