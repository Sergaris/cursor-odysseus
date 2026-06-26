"""Тесты stream_bridge для Cursor SDK."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.cursor_sdk.stream_bridge import release_chat_session, stream_cursor_sdk


@pytest.mark.asyncio
async def test_stream_cursor_sdk_yields_deltas_and_done(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test_key")

    async def fake_stream_chat(**kwargs):
        yield f'data: {json.dumps({"delta": "Hello"})}\n\n'
        yield f'data: {json.dumps({"delta": " world"})}\n\n'

    with patch("src.cursor_sdk.stream_bridge._stream_chat", side_effect=fake_stream_chat):
        events = []
        async for chunk in stream_cursor_sdk(
            messages=[{"role": "user", "content": "hi"}],
            model="composer-2.5",
            cwd="/tmp/ws",
            headers={"X-Cursor-Api-Key": "test_key"},
            session_id="sess-1",
            timeout=30,
        ):
            for line in chunk.split("\n"):
                line = line.strip()
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    events.append(json.loads(line[6:]))
                if line == "data: [DONE]":
                    events.append({"done": True})

    assert events == [{"delta": "Hello"}, {"delta": " world"}, {"done": True}]


@pytest.mark.asyncio
async def test_stream_cursor_sdk_auth_error(monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    from src.cursor_sdk.auth import CursorSDKAuthError

    with patch(
        "src.cursor_sdk.stream_bridge.resolve_api_key",
        side_effect=CursorSDKAuthError("missing key"),
    ):
        events = []
        async for chunk in stream_cursor_sdk(
            messages=[{"role": "user", "content": "hi"}],
            model="composer-2.5",
            cwd="/tmp/ws",
            headers={},
            session_id="sess-2",
            timeout=15,
        ):
            events.append(chunk)

    assert any("event: error" in chunk for chunk in events)
    assert any("503" in chunk for chunk in events)


@pytest.mark.asyncio
async def test_release_chat_session_schedules_task():
    with patch(
        "src.cursor_sdk.stream_bridge.AsyncSessionRegistryManager.release_chat",
        new_callable=AsyncMock,
    ) as release_mock:
        release_chat_session("sess-99")
        await asyncio.sleep(0.05)
    release_mock.assert_awaited_once_with("sess-99")
