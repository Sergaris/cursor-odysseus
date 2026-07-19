"""Strategy-tagged queries route to academic adapters in DeepResearcher."""

import pytest
from unittest.mock import patch

from src.deep_research import DeepResearcher


def test_parse_query_items_accepts_objects_and_strings():
    r = object.__new__(DeepResearcher)
    items = r._parse_query_items(
        '[{"query": "q1 site:arxiv.org", "strategy": "academic"}, "plain q"]'
    )
    assert items[0]["query"] == "q1 site:arxiv.org"
    assert items[0]["strategy"] == "academic"
    assert items[1]["query"] == "plain q"
    assert items[1]["strategy"] == "web"


def test_infer_strategy_from_operators():
    assert DeepResearcher._infer_strategy("x site:github.com/issues") == "github"
    assert DeepResearcher._infer_strategy("x filetype:pdf") == "pdf"
    assert DeepResearcher._infer_strategy("x after:2025-01-01") == "news"


@pytest.mark.asyncio
async def test_search_routes_academic_strategy():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
    )
    researcher._query_strategies["topic site:arxiv.org"] = "academic"

    with patch(
        "services.search.academic.search_academic",
        return_value=[{"url": "https://arxiv.org/abs/1", "title": "P"}],
    ) as academic:
        results = await researcher._search("topic site:arxiv.org")

    assert results[0]["url"].startswith("https://arxiv.org/")
    academic.assert_called_once()
    assert "academic" in researcher.providers_used
