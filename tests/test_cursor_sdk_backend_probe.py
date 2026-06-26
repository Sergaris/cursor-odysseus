"""Tests for Cursor SDK backend probe behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cursor_sdk.backend import CursorSDKBackend


@pytest.mark.asyncio
async def test_probe_routing_alias_checks_bridge_only(monkeypatch):
    backend = CursorSDKBackend.__new__(CursorSDKBackend)
    backend.model = "default"
    backend.cwd = "/tmp/workspace"
    backend._api_key = "key"

    get_client = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr("src.cursor_sdk.backend.get_async_client", get_client)

    with patch.object(CursorSDKBackend, "complete", new=AsyncMock()) as complete:
        await backend.probe(timeout=15)

    get_client.assert_awaited_once_with("/tmp/workspace")
    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_concrete_model_uses_ephemeral_completion(monkeypatch):
    backend = CursorSDKBackend.__new__(CursorSDKBackend)
    backend.model = "composer-2.5"
    backend.cwd = "/tmp/workspace"
    backend._api_key = "key"

    complete_ephemeral = AsyncMock(return_value="ok")
    monkeypatch.setattr("src.cursor_sdk.backend._complete_ephemeral", complete_ephemeral)
    monkeypatch.setattr(
        "src.cursor_sdk.backend.retry_after_bridge_transport_error",
        AsyncMock(return_value="no"),
    )

    with patch.object(CursorSDKBackend, "complete", new=AsyncMock()) as complete:
        await backend.probe(timeout=15)

    complete.assert_not_awaited()
    complete_ephemeral.assert_awaited_once()
    assert complete_ephemeral.await_args.kwargs["model"] == "composer-2.5"
    assert complete_ephemeral.await_args.kwargs["prompt"] == "Reply with exactly: ok"
