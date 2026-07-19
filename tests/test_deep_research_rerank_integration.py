"""Reranking is applied in the Deep Research search path before fetch."""

import asyncio
import time
from unittest.mock import patch

import pytest

from src.deep_research import DeepResearcher


@pytest.mark.asyncio
async def test_search_ranks_provider_results_before_return():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
    )

    raw = [
        {"url": "https://low.example/a", "title": "unrelated", "snippet": "zzz"},
        {
            "url": "https://high.example/b",
            "title": "quantum computing breakthrough",
            "snippet": "quantum computing breakthrough details",
        },
    ]

    def fake_call_provider(prov, query, count, time_filter=None):
        return list(raw)

    def fake_rank(query, results):
        # Put the high-relevance result first.
        return sorted(
            results,
            key=lambda r: 0 if "quantum" in (r.get("title") or "").lower() else 1,
        )

    with (
        patch("src.search.providers._get_search_settings", return_value={"search_provider": "brave"}),
        patch("src.search.core._build_provider_chain", return_value=["brave"]),
        patch("src.search.core._call_provider", side_effect=fake_call_provider),
        patch("services.search.ranking.rank_search_results", side_effect=fake_rank),
    ):
        results = await researcher._search("quantum computing breakthrough")

    assert results[0]["url"] == "https://high.example/b"


@pytest.mark.asyncio
async def test_search_and_extract_ranks_pool_before_fetch():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        max_urls_per_query=1,
        extraction_concurrency=1,
    )
    researcher._start_time = time.time()
    fetched: list[str] = []

    async def fake_search(query: str):
        return [
            {"url": "https://low.example/a", "title": "noise"},
            {"url": "https://high.example/b", "title": "exact match topic"},
        ]

    async def fake_fetch(url, question, title):
        fetched.append(url)
        return {"url": url, "title": title, "summary": "ok"}

    def fake_rank(query, results):
        return sorted(
            results,
            key=lambda r: 0 if "high.example" in r.get("url", "") else 1,
        )

    researcher._search = fake_search  # type: ignore[method-assign]
    researcher._fetch_and_extract = fake_fetch  # type: ignore[method-assign]

    with patch("services.search.ranking.rank_search_results", side_effect=fake_rank):
        findings = await researcher._search_and_extract(["topic"], "topic")

    assert len(findings) == 1
    assert fetched == ["https://high.example/b"]
