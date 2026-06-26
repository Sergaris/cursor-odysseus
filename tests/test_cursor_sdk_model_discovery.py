"""Тесты live discovery моделей Cursor SDK."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cursor_sdk.model_discovery import list_cursor_sdk_model_ids, list_cursor_sdk_models_async


@pytest.mark.asyncio
async def test_list_cursor_sdk_models_async_returns_sorted_unique_ids():
    model_a = MagicMock(id="composer-2.5")
    model_b = MagicMock(id="gpt-5.5")
    model_dup = MagicMock(id="composer-2.5")

    with patch(
        "src.cursor_sdk.async_runtime.get_async_client",
        new_callable=AsyncMock,
        return_value=MagicMock(),
    ), patch(
        "cursor_sdk.asyncio.AsyncCursor.models.list",
        new_callable=AsyncMock,
        return_value=[model_b, model_a, model_dup],
    ), patch(
        "src.cursor_sdk.model_discovery.resolve_api_key",
        return_value="test-key",
    ), patch(
        "src.cursor_sdk.model_discovery.resolve_cursor_sdk_cwd",
        return_value="/tmp/ws",
    ):
        ids = await list_cursor_sdk_models_async(api_key="test-key", base_url="cursor-sdk://local")

    assert ids == ["composer-2.5", "gpt-5.5"]


def test_list_cursor_sdk_model_ids_sync_delegates_to_async():
    with patch(
        "src.cursor_sdk.model_discovery._run_on_loop",
        return_value=["composer-2.5", "default"],
    ) as run_mock:
        ids = list_cursor_sdk_model_ids(api_key="k", base_url="cursor-sdk://local", timeout=10)

    assert ids == ["composer-2.5", "default"]
    run_mock.assert_called_once()
