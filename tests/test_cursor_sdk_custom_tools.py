"""Тесты Odysseus custom_tools для Cursor SDK."""

from unittest.mock import AsyncMock

import pytest

from src.cursor_sdk.custom_tools import CURSOR_SDK_TOOL_NAMES, build_custom_tools


def test_build_custom_tools_includes_web_search():
    tools = build_custom_tools()
    assert "web_search" in tools
    assert tools["web_search"].description
    assert tools["web_search"].input_schema
    assert callable(tools["web_search"].execute)


def test_build_custom_tools_respects_subset():
    subset = frozenset({"web_search", "manage_calendar"})
    tools = build_custom_tools(subset)
    assert set(tools.keys()) == {"web_search", "manage_calendar"}


@pytest.mark.asyncio
async def test_custom_tool_uses_runtime_holder():
    from src.cursor_sdk.tool_runtime import CursorSdkToolRuntime, RuntimeHolder

    holder = RuntimeHolder()
    tools = build_custom_tools(frozenset({"web_search"}), runtime_holder=holder)
    runtime = CursorSdkToolRuntime(session_id="sess-1")
    holder.runtime = runtime

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.tool_execution.execute_tool_block",
            AsyncMock(return_value=("web_search", {"output": "ok", "exit_code": 0})),
        )
        result = await tools["web_search"].execute({"query": "weather"}, None)

    assert result["content"][0]["text"] == "ok"
    assert "web_search" in runtime.tools_executed
    assert len(runtime.tool_events) == 1
    assert runtime.tool_events[0]["tool"] == "web_search"
    assert runtime.tool_events[0]["command"] == "weather"
    assert runtime.tool_events[0]["output"] == "ok"
    assert runtime.tool_events[0]["exit_code"] == 0


def test_cursor_sdk_tool_names_cover_assistant_basics():
    for name in ("web_search", "web_fetch", "manage_calendar", "list_emails"):
        assert name in CURSOR_SDK_TOOL_NAMES


def test_is_tool_allowed_ignores_relevant_tools_for_sdk_tools():
    from src.cursor_sdk.tool_runtime import CursorSdkToolRuntime, is_tool_allowed

    runtime = CursorSdkToolRuntime(
        relevant_tools=frozenset({"manage_memory", "web_search"}),
    )
    assert is_tool_allowed("ui_control", runtime) is True
    assert is_tool_allowed("manage_notes", runtime) is True
    assert is_tool_allowed("manage_memory", runtime) is True
