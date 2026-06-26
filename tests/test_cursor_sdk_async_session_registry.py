"""Тесты профилей AsyncSessionRegistry (research text-only vs chat tools)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cursor_sdk.async_session_registry import (
    _AGENT_PROFILE_TEXT_ONLY,
    _AGENT_PROFILE_TOOLS,
    _expected_agent_profile,
    AsyncSessionRegistry,
)


def test_expected_agent_profile():
    assert _expected_agent_profile("research") == _AGENT_PROFILE_TEXT_ONLY
    assert _expected_agent_profile("chat") == _AGENT_PROFILE_TOOLS
    assert _expected_agent_profile("ephemeral") == _AGENT_PROFILE_TOOLS


@pytest.mark.asyncio
async def test_get_agent_research_uses_text_only_profile():
    registry = AsyncSessionRegistry(api_key="k", model="composer-2.5", cwd="/tmp/ws")
    client = MagicMock()
    created_agent = MagicMock()
    created_agent._odysseus_agent_profile = _AGENT_PROFILE_TEXT_ONLY

    with patch(
        "cursor_sdk.asyncio.AsyncAgent.create",
        new_callable=AsyncMock,
        return_value=created_agent,
    ) as create_mock, patch(
        "src.cursor_sdk.custom_tools.build_custom_tools",
    ) as build_tools:
        agent = await registry.get_agent("research", "job-1", client=client)

    assert agent is created_agent
    create_mock.assert_awaited_once()
    local_opts = create_mock.await_args.kwargs["local"]
    assert getattr(local_opts, "setting_sources", None) == []
    assert getattr(local_opts, "custom_tools", None) is None
    build_tools.assert_not_called()


@pytest.mark.asyncio
async def test_get_agent_chat_uses_tools_profile():
    registry = AsyncSessionRegistry(api_key="k", model="composer-2.5", cwd="/tmp/ws")
    client = MagicMock()
    created_agent = MagicMock()
    created_agent._odysseus_runtime_holder = MagicMock()
    created_agent._odysseus_agent_profile = _AGENT_PROFILE_TOOLS

    fake_tools = {"web_search": MagicMock()}

    with patch(
        "cursor_sdk.asyncio.AsyncAgent.create",
        new_callable=AsyncMock,
        return_value=created_agent,
    ) as create_mock, patch(
        "src.cursor_sdk.custom_tools.build_custom_tools",
        return_value=fake_tools,
    ) as build_tools:
        agent = await registry.get_agent("chat", "sess-1", client=client)

    assert agent is created_agent
    build_tools.assert_called_once()
    create_mock.assert_awaited_once()
    local_opts = create_mock.await_args.kwargs["local"]
    assert getattr(local_opts, "custom_tools", None) == fake_tools


@pytest.mark.asyncio
async def test_get_agent_recreates_on_profile_mismatch():
    registry = AsyncSessionRegistry(api_key="k", model="composer-2.5", cwd="/tmp/ws")
    client = MagicMock()

    stale_agent = MagicMock()
    stale_agent._odysseus_agent_profile = _AGENT_PROFILE_TOOLS
    registry._agents[("research", "job-1")] = stale_agent

    fresh_agent = MagicMock()
    fresh_agent._odysseus_agent_profile = _AGENT_PROFILE_TEXT_ONLY

    with patch(
        "cursor_sdk.asyncio.AsyncAgent.create",
        new_callable=AsyncMock,
        return_value=fresh_agent,
    ), patch.object(registry, "release", new_callable=AsyncMock) as release_mock:
        agent = await registry.get_agent("research", "job-1", client=client)

    release_mock.assert_awaited_once_with("research", "job-1")
    assert agent is fresh_agent
