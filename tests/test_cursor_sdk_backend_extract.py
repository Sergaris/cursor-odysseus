"""Tests for Cursor SDK run text extraction."""

import pytest

from src.cursor_sdk.backend import _extract_run_text


@pytest.mark.asyncio
async def test_extract_run_text_handles_async_iter_text():
    async def _iter_text():
        yield "hello "
        yield "async"

    class _Run:
        def iter_text(self):
            return _iter_text()

        def messages(self):
            return iter(())

    assert await _extract_run_text(_Run()) == "hello async"


@pytest.mark.asyncio
async def test_extract_run_text_handles_async_messages_fallback():
    async def _messages():
        block = type("Block", (), {"type": "text", "text": "hello async"})()
        inner = type("Inner", (), {"content": [block]})()
        message = type("Msg", (), {"type": "assistant", "message": inner})()
        yield message

    class _Run:
        def iter_text(self):
            return iter(())

        def messages(self):
            return _messages()

    assert await _extract_run_text(_Run()) == "hello async"
