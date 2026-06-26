"""Тесты stream_llm для cursor-sdk provider."""

import asyncio
import json
from unittest.mock import patch

import pytest

from src import llm_core
from src.cursor_sdk.provider import CURSOR_SDK_BASE_URL


@pytest.mark.asyncio
async def test_stream_llm_cursor_sdk_delegates_to_bridge():
    bridge_chunks = [
        'data: {"delta": "Hi"}\n\n',
        "data: [DONE]\n\n",
    ]

    async def fake_bridge(**kwargs):
        for chunk in bridge_chunks:
            yield chunk

    with patch("src.cursor_sdk.stream_bridge.stream_cursor_sdk", side_effect=fake_bridge):
        collected = []
        async for chunk in llm_core.stream_llm(
            CURSOR_SDK_BASE_URL,
            "composer-2.5",
            [{"role": "user", "content": "hello"}],
            headers={"X-Cursor-Api-Key": "k"},
            session_id="chat-sess-1",
        ):
            collected.append(chunk)

    assert collected == bridge_chunks


def test_stream_llm_cursor_sdk_parsed_events():
    async def fake_bridge(**kwargs):
        yield 'data: {"delta": "stream"}\n\n'
        yield "data: [DONE]\n\n"

    with patch("src.cursor_sdk.stream_bridge.stream_cursor_sdk", side_effect=fake_bridge):
        async def run():
            events = []
            async for chunk in llm_core.stream_llm(
                CURSOR_SDK_BASE_URL,
                "composer-2.5",
                [{"role": "user", "content": "hello"}],
                session_id="s1",
            ):
                for line in chunk.split("\n"):
                    line = line.strip()
                    if line.startswith("data: ") and line[6:] != "[DONE]":
                        events.append(json.loads(line[6:]))
            return events

        events = asyncio.run(run())

    assert events == [{"delta": "stream"}]
