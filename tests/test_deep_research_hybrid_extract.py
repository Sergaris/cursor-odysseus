"""Hybrid extract: per-URL extraction через HTTP при cursor brain."""

import pytest
from unittest.mock import AsyncMock, patch

from src.deep_research import DeepResearcher


@pytest.mark.asyncio
async def test_fetch_and_extract_uses_http_when_hybrid_configured():
    researcher = DeepResearcher(
        llm_endpoint="cursor-sdk://local",
        llm_model="composer-2.5",
        cursor_backend=object(),
        extract_llm_endpoint="http://localhost:11434/v1/chat/completions",
        extract_llm_model="llama3",
        extract_llm_headers={"Authorization": "Bearer test"},
    )

    page = {
        "success": True,
        "content": "Garmin Fenix 8 battery drain after GPS workouts.",
        "title": "Battery tips",
    }

    with patch(
        "src.deep_research.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=page,
    ), patch.object(
        researcher,
        "_llm",
        new_callable=AsyncMock,
    ) as brain_llm, patch.object(
        researcher,
        "_llm_extract",
        new_callable=AsyncMock,
        return_value='{"summary":"GPS drain fix","evidence":"disable wrist HR"}',
    ) as extract_llm:
        result = await researcher._fetch_and_extract(
            "https://example.com/battery",
            "Garmin Fenix 8 battery drain",
            "Battery tips",
        )

    assert result is not None
    extract_llm.assert_awaited_once()
    brain_llm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_extract_falls_back_to_brain_without_hybrid():
    researcher = DeepResearcher(
        llm_endpoint="cursor-sdk://local",
        llm_model="composer-2.5",
        cursor_backend=object(),
    )

    with patch.object(
        researcher,
        "_llm",
        new_callable=AsyncMock,
        return_value="brain response",
    ) as brain_llm:
        text = await researcher._llm_extract([{"role": "user", "content": "extract"}])

    assert text == "brain response"
    brain_llm.assert_awaited_once()
