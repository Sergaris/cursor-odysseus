"""Async реестр Cursor SDK agent-сессий."""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cursor_sdk.asyncio import AsyncAgent, AsyncClient

logger = logging.getLogger(__name__)

_AGENT_PROFILE_TEXT_ONLY = "text_only"
_AGENT_PROFILE_TOOLS = "tools"
_RESEARCH_SCOPE = "research"


def _expected_agent_profile(scope: str) -> str:
    """Профиль agent для scope: DR — только текст, chat — с custom_tools."""
    if scope == _RESEARCH_SCOPE:
        return _AGENT_PROFILE_TEXT_ONLY
    return _AGENT_PROFILE_TOOLS


class AsyncSessionRegistry:
    """Хранит AsyncAgent по паре (scope, scope_id)."""

    def __init__(self, *, api_key: str, model: str, cwd: str) -> None:
        from src.cursor_sdk.provider import normalize_cursor_sdk_model

        self._api_key = api_key
        self._model = normalize_cursor_sdk_model(model)
        self._cwd = cwd
        self._agents: dict[tuple[str, str], "AsyncAgent"] = {}
        self._active_runs: dict[tuple[str, str], Any] = {}

    def set_active_run(self, scope: str, scope_id: str, run: Any) -> None:
        """Запоминает активный run для cooperative cancel."""
        self._active_runs[(scope, scope_id)] = run

    def clear_active_run(self, scope: str, scope_id: str) -> None:
        """Удаляет ссылку на run после завершения."""
        self._active_runs.pop((scope, scope_id), None)

    async def cancel_active(self, scope: str, scope_id: str) -> None:
        """Отменяет текущий run, если SDK его ещё выполняет."""
        run = self._active_runs.pop((scope, scope_id), None)
        if run is None:
            return
        cancel = getattr(run, "cancel", None)
        if cancel is None:
            return
        try:
            await cancel()
            logger.info("Cursor SDK run отменён: scope=%s scope_id=%s", scope, scope_id)
        except Exception as exc:
            logger.warning("Не удалось отменить Cursor SDK run: %s", exc)

    async def get_agent(
        self,
        scope: str,
        scope_id: str,
        *,
        client: "AsyncClient",
    ) -> "AsyncAgent":
        """Возвращает существующий или создаёт новый AsyncAgent."""
        key = (scope, scope_id)
        expected_profile = _expected_agent_profile(scope)
        agent = self._agents.get(key)
        if agent is not None:
            if getattr(agent, "_odysseus_agent_profile", None) != expected_profile:
                await self.release(scope, scope_id)
                agent = None
            else:
                return agent

        from cursor_sdk import LocalAgentOptions
        from cursor_sdk.asyncio import AsyncAgent

        if expected_profile == _AGENT_PROFILE_TEXT_ONLY:
            agent = await AsyncAgent.create(
                client=client,
                api_key=self._api_key,
                model=self._model,
                local=LocalAgentOptions(
                    cwd=self._cwd,
                    setting_sources=[],
                ),
            )
            agent._odysseus_agent_profile = _AGENT_PROFILE_TEXT_ONLY
            self._agents[key] = agent
            logger.info(
                "Cursor SDK async agent создан: scope=%s scope_id=%s profile=text_only",
                scope,
                scope_id,
            )
            return agent

        from src.cursor_sdk.custom_tools import build_custom_tools
        from src.cursor_sdk.tool_runtime import RuntimeHolder

        runtime_holder = RuntimeHolder()
        custom_tools = build_custom_tools(runtime_holder=runtime_holder)
        agent = await AsyncAgent.create(
            {"mode": "agent"},
            client=client,
            api_key=self._api_key,
            model=self._model,
            local=LocalAgentOptions(
                cwd=self._cwd,
                setting_sources=[],
                custom_tools=custom_tools,
            ),
        )
        agent._odysseus_runtime_holder = runtime_holder
        agent._odysseus_agent_profile = _AGENT_PROFILE_TOOLS
        self._agents[key] = agent
        logger.info(
            "Cursor SDK async agent создан: scope=%s scope_id=%s custom_tools=%s mode=agent profile=tools",
            scope,
            scope_id,
            len(custom_tools),
        )
        return agent

    async def release(self, scope: str, scope_id: str) -> None:
        """Закрывает agent и удаляет его из реестра."""
        key = (scope, scope_id)
        agent = self._agents.pop(key, None)
        if agent is None:
            return
        try:
            await agent.close()
            logger.info("Cursor SDK async agent закрыт: scope=%s scope_id=%s", scope, scope_id)
        except Exception as exc:
            logger.warning("Не удалось закрыть Cursor SDK async agent: %s", exc)

    async def release_all(self) -> None:
        """Закрывает все агенты."""
        keys = list(self._agents.keys())
        for scope, scope_id in keys:
            await self.release(scope, scope_id)
