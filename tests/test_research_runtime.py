"""Tests for Deep Research timeout resolution (0 = unlimited)."""

from src.deep_research import DeepResearcher
from src.research_handler import _resolve_research_max_time, _resolve_research_timeout


def test_resolve_research_timeout_zero_means_unlimited():
    assert _resolve_research_timeout(0) is None
    assert _resolve_research_timeout(-1) is None
    assert _resolve_research_timeout(None, default=0) is None


def test_resolve_research_timeout_positive_passthrough():
    assert _resolve_research_timeout(120) == 120


def test_resolve_research_max_time_zero_means_unlimited_loop():
    assert _resolve_research_max_time(0) == 0
    assert _resolve_research_max_time(None, default=0) == 0


def test_deep_researcher_unlimited_llm_timeouts():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=0,
        planning_timeout=0,
        query_timeout=0,
        heavy_llm_timeout=0,
    )
    assert researcher.extraction_timeout is None
    assert researcher.planning_timeout is None
    assert researcher.query_timeout is None
    assert researcher.heavy_llm_timeout is None
