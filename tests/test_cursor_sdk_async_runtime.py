"""Tests for bridge transport error detection and reconnect."""

import pytest
from unittest import mock
from unittest.mock import AsyncMock, patch

from src.cursor_sdk.async_runtime import (
    drop_async_client,
    get_async_client,
    is_bridge_process_alive,
    is_bridge_transport_error,
    reset_async_clients_for_tests,
    retry_after_bridge_transport_error,
)


def test_is_bridge_transport_error_matches_connect_message():
    assert is_bridge_transport_error(RuntimeError("ConnectError: All connection attempts failed"))


def test_is_bridge_transport_error_matches_read_error():
    assert is_bridge_transport_error(RuntimeError("Bridge request failed: ReadError: "))


def test_is_bridge_transport_error_ignores_unrelated():
    assert is_bridge_transport_error(ValueError("bad model")) is False


@pytest.mark.asyncio
async def test_drop_async_client_allows_relaunch():
    reset_async_clients_for_tests()
    dead = AsyncMock()
    dead.aclose = AsyncMock()
    from src.cursor_sdk import async_runtime as rt

    rt._CLIENTS[r"C:\workspace"] = dead
    await drop_async_client(r"C:\workspace")
    dead.aclose.assert_awaited_once()
    assert r"C:\workspace" not in rt._CLIENTS

    calls = {"n": 0}

    async def fake_launch(**kwargs):
        calls["n"] += 1
        client = AsyncMock()
        client.aclose = AsyncMock()
        return client

    with patch("cursor_sdk.asyncio.AsyncClient.launch_bridge", fake_launch):
        client = await get_async_client(r"C:\workspace")
    assert client is not None
    assert calls["n"] == 1
    reset_async_clients_for_tests()


@pytest.mark.asyncio
async def test_get_async_client_retries_launch(monkeypatch):
    reset_async_clients_for_tests()
    calls = {"n": 0}

    async def fake_launch(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("Missing value for --tool-callback-auth-token")
        client = AsyncMock()
        client.aclose = AsyncMock()
        return client

    monkeypatch.setattr(
        "cursor_sdk.asyncio.AsyncClient.launch_bridge",
        fake_launch,
    )

    client = await get_async_client(r"C:\workspace")
    assert client is not None
    assert calls["n"] == 2

    reset_async_clients_for_tests()


@pytest.mark.asyncio
async def test_drop_async_client_respects_cooldown():
    reset_async_clients_for_tests()
    dead = AsyncMock()
    dead.aclose = AsyncMock()
    from src.cursor_sdk import async_runtime as rt

    rt._CLIENTS[r"C:\workspace"] = dead
    rt._last_bridge_drop_at = rt.time.monotonic()

    await drop_async_client(r"C:\workspace")
    dead.aclose.assert_not_awaited()
    assert r"C:\workspace" in rt._CLIENTS

    await drop_async_client(r"C:\workspace", force=True)
    dead.aclose.assert_awaited_once()
    reset_async_clients_for_tests()


@pytest.mark.asyncio
async def test_retry_after_bridge_transport_error_relaunch_when_dead():
    reset_async_clients_for_tests()
    action = await retry_after_bridge_transport_error(
        r"C:\workspace",
        RuntimeError("ConnectError: All connection attempts failed"),
    )
    assert action == "relaunch"
    reset_async_clients_for_tests()


@pytest.mark.asyncio
async def test_retry_after_bridge_transport_error_retry_when_alive():
    reset_async_clients_for_tests()
    proc = mock.Mock(returncode=None)
    bridge = mock.Mock(process=proc)
    client = mock.Mock(_owned_bridge=bridge)
    from src.cursor_sdk import async_runtime as rt

    rt._CLIENTS[r"C:\workspace"] = client
    assert is_bridge_process_alive(r"C:\workspace") is True

    with mock.patch.object(rt, "drop_async_client", AsyncMock()) as drop_mock:
        action = await retry_after_bridge_transport_error(
            r"C:\workspace",
            RuntimeError("ReadError"),
        )
    assert action == "retry"
    drop_mock.assert_not_awaited()
    reset_async_clients_for_tests()
