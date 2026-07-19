"""Тесты lifecycle Deep Research с Cursor SDK backend (mock)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.research_handler import ResearchHandler, _is_cursor_research_brain


@pytest.mark.asyncio
async def test_call_research_service_cursor_cleanup(monkeypatch):
    mock_backend = MagicMock()
    mock_backend.probe = AsyncMock()
    mock_backend.close = MagicMock()
    mock_registry = MagicMock()

    with patch(
        "src.research_handler._build_cursor_research_backend",
        return_value=(mock_backend, mock_registry),
    ), patch("src.deep_research.DeepResearcher") as mock_researcher_cls:
        researcher = MagicMock()
        researcher.research = AsyncMock(return_value="report body")
        researcher.get_stats.return_value = {"Findings": 1}
        mock_researcher_cls.return_value = researcher

        handler = ResearchHandler()
        entry = {}
        result = await handler.call_research_service(
            query="test query",
            llm_endpoint="cursor-sdk://local",
            llm_model="composer-2.5",
            _task_entry=entry,
            session_id="rp-test123",
        )

    mock_backend.probe.assert_not_called()
    mock_backend.close.assert_called_once()
    assert entry.get("cursor_backend") is mock_backend
    assert "report body" in result


def test_is_cursor_research_brain_default_http(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: "http" if key == "research_brain_provider" else default,
        raising=False,
    )
    assert _is_cursor_research_brain() is False
