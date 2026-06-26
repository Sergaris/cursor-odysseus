"""Тесты ветки cursor-sdk в llm_core."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.cursor_sdk.provider import CURSOR_SDK_BASE_URL
from src.llm_core import _detect_provider, llm_call_async


def test_detect_provider_cursor_sdk():
    assert _detect_provider(CURSOR_SDK_BASE_URL) == "cursor-sdk"
    assert _detect_provider(f"{CURSOR_SDK_BASE_URL}?cwd=/tmp/ws") == "cursor-sdk"


@pytest.mark.asyncio
async def test_llm_call_async_cursor_sdk_skips_cache(monkeypatch):
    mock_backend = MagicMock()
    mock_backend.complete = AsyncMock(return_value="hello from composer")

    with patch("src.llm_core._get_cached_response", return_value="stale") as cache_get, patch(
        "src.llm_core._set_cached_response"
    ) as cache_set, patch(
        "src.cursor_sdk.backend.CursorSDKBackend",
        return_value=mock_backend,
    ):
        result = await llm_call_async(
            CURSOR_SDK_BASE_URL,
            "composer-2.5",
            [{"role": "user", "content": "hi"}],
            headers={"X-Cursor-Api-Key": "test_key"},
            timeout=15,
        )

    assert result == "hello from composer"
    cache_get.assert_not_called()
    cache_set.assert_not_called()
    mock_backend.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_call_async_cursor_sdk_run_error():
    from src.cursor_sdk.backend import CursorSDKRunError

    mock_backend = MagicMock()
    mock_backend.complete = AsyncMock(side_effect=CursorSDKRunError("run failed"))

    with patch("src.cursor_sdk.backend.CursorSDKBackend", return_value=mock_backend):
        with pytest.raises(HTTPException) as exc:
            await llm_call_async(
                CURSOR_SDK_BASE_URL,
                "composer-2.5",
                [{"role": "user", "content": "hi"}],
                headers={"Authorization": "Bearer test_key"},
            )

    assert exc.value.status_code == 502
