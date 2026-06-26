"""Реестр долгоживущих Cursor SDK agent-сессий."""

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class SessionRegistry:
    """Хранит Agent.create() по паре (scope, scope_id).

    Scope `research` — один agent на research job.
    Scope `chat` — один agent на Odysseus session_id.
    Scope `ephemeral` не кэшируется здесь — для one-shot используется Agent.prompt().
    """

    def __init__(self, *, api_key: str, model: str, cwd: str) -> None:
        self._api_key = api_key
        self._model = model
        self._cwd = cwd
        self._agents: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    def get_agent(self, scope: str, scope_id: str) -> Any:
        """Возвращает существующий или создаёт новый Agent для scope."""
        key = (scope, scope_id)
        with self._lock:
            agent = self._agents.get(key)
            if agent is not None:
                return agent
            agent = self._create_agent()
            self._agents[key] = agent
            logger.info("Cursor SDK agent создан: scope=%s scope_id=%s", scope, scope_id)
            return agent

    def release(self, scope: str, scope_id: str) -> None:
        """Закрывает agent и удаляет его из реестра."""
        key = (scope, scope_id)
        with self._lock:
            agent = self._agents.pop(key, None)
        if agent is None:
            return
        try:
            agent.close()
            logger.info("Cursor SDK agent закрыт: scope=%s scope_id=%s", scope, scope_id)
        except Exception as exc:
            logger.warning("Не удалось закрыть Cursor SDK agent: %s", exc)

    def release_all(self) -> None:
        """Закрывает все агенты в реестре."""
        with self._lock:
            keys = list(self._agents.keys())
        for scope, scope_id in keys:
            self.release(scope, scope_id)

    def _create_agent(self) -> Any:
        from cursor_sdk import Agent, LocalAgentOptions

        return Agent.create(
            model=self._model,
            api_key=self._api_key,
            local=LocalAgentOptions(cwd=self._cwd, setting_sources=[]),
        )
