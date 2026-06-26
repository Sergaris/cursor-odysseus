"""Тесты принудительного вызова Odysseus tools для Cursor SDK."""

import json

from src.cursor_sdk.tool_enforcer import (
    inject_cursor_sdk_tool_directive,
    plan_mandatory_tool_blocks,
    plan_postflight_tool_blocks,
)
from src.agent_tools import ToolBlock


def test_inject_cursor_sdk_tool_directive_appends_to_system():
    messages = [{"role": "system", "content": "Base prompt."}, {"role": "user", "content": "Hi"}]
    out = inject_cursor_sdk_tool_directive(messages)
    assert "CURSOR SDK AGENT MODE" in out[0]["content"]
    assert "native custom tools" in out[0]["content"]


def test_weather_query_plans_web_search():
    blocks = plan_mandatory_tool_blocks(
        user_message="какая погода завтра в мск?",
        relevant_tools={"web_search", "web_fetch"},
        disabled_tools=set(),
        tool_policy=None,
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "погода" in blocks[0].content


def test_forced_web_search_toggle():
    blocks = plan_mandatory_tool_blocks(
        user_message="hello",
        relevant_tools={"web_search"},
        disabled_tools=set(),
        tool_policy=None,
        forced_tools={"web_search", "web_fetch"},
    )
    assert blocks == [ToolBlock("web_search", "hello")]


def test_calendar_lookup_plans_list_events():
    blocks = plan_mandatory_tool_blocks(
        user_message="What's on my calendar tomorrow?",
        relevant_tools={"manage_calendar"},
        disabled_tools=set(),
        tool_policy=None,
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "manage_calendar"
    payload = json.loads(blocks[0].content)
    assert payload["action"] == "list_events"


def test_calendar_create_not_preflighted():
    blocks = plan_mandatory_tool_blocks(
        user_message="Add lunch to my calendar tomorrow at 1pm",
        relevant_tools={"manage_calendar"},
        disabled_tools=set(),
        tool_policy=None,
    )
    assert blocks == []


def test_check_inbox_plans_list_emails():
    blocks = plan_mandatory_tool_blocks(
        user_message="check my inbox",
        relevant_tools={"list_emails"},
        disabled_tools=set(),
        tool_policy=None,
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "list_emails"


def test_explicit_url_prefers_web_fetch():
    blocks = plan_mandatory_tool_blocks(
        user_message="What does https://example.com say?",
        relevant_tools={"web_search", "web_fetch"},
        disabled_tools=set(),
        tool_policy=None,
    )
    assert blocks == [ToolBlock("web_fetch", "https://example.com")]


def test_postflight_skips_already_executed():
    blocks = plan_postflight_tool_blocks(
        user_message="What's the weather in NYC?",
        relevant_tools={"web_search"},
        disabled_tools=set(),
        tool_policy=None,
        forced_tools=None,
        executed_tools={"web_search"},
    )
    assert blocks == []


def test_respects_disabled_tools():
    blocks = plan_mandatory_tool_blocks(
        user_message="What's the weather?",
        relevant_tools={"web_search"},
        disabled_tools={"web_search"},
        tool_policy=None,
    )
    assert blocks == []
