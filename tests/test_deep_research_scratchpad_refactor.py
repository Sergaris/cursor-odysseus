"""Tests for Deep Research scratchpad refactor."""
import asyncio
import json
from pathlib import Path

import pytest

from src.deep_research import DeepResearcher


def _researcher(**kwargs) -> DeepResearcher:
    r = DeepResearcher.__new__(DeepResearcher)
    defaults = {
        "llm_endpoint": "http://local.test/v1/chat/completions",
        "llm_model": "local-model",
        "max_urls_per_query": 5,
        "queries_round1": 5,
        "queries_followup": 4,
        "scratchpad_max_tokens": 4096,
        "max_report_tokens": 4096,
        "heavy_llm_timeout": 600,
        "findings": [],
        "plan_sub_questions": ["What is X?", "How does Y work?"],
        "_step_counter": 0,
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(r, k, v)
    return r


_FINDINGS = [
    {
        "url": "https://ex.com/a",
        "title": "Source A",
        "summary": "Fact about X.",
        "evidence": "Long quote about X from source A with full context.",
    },
    {
        "url": "https://ex.com/b",
        "title": "Source B",
        "summary": "Fact about Y.",
        "evidence": "Long quote about Y from source B.",
    },
]


def test_scratchpad_tracks_sub_question_coverage():
    r = _researcher()
    scratchpad_json = {
        "sub_topics": [
            {
                "question": "What is X?",
                "status": "covered",
                "key_facts": ["X is defined [https://ex.com/a]"],
                "gaps": [],
                "conflicts": [],
            },
            {
                "question": "How does Y work?",
                "status": "partial",
                "key_facts": [
                    "Y uses Z [https://ex.com/b]",
                    "Y also needs W [https://other.com/c]",
                ],
                "gaps": ["pricing"],
                "conflicts": [],
            },
        ],
        "insights": ["Key discovery about X."],
    }
    scratchpad = json.dumps(scratchpad_json)
    assert r._scratchpad_coverage_ok(scratchpad) is True
    insights = r._extract_insights_for_ui(scratchpad)
    assert insights == ["Key discovery about X."]
    gaps = r._extract_gaps_from_scratchpad(scratchpad)
    assert gaps == ["pricing"]


def test_final_answer_no_word_limits():
    source = Path("src/deep_research.py").read_text(encoding="utf-8")
    assert "1500 words" not in source
    assert "MINIMUM 1500" not in source
    assert "too brief. Please expand" not in source
    assert "Target at least 1000 words" not in source
    assert "CATEGORY_PROMPTS" not in source
    assert "_classify_category" not in source
    assert "_final_report" not in source
    assert "async def _synthesize" not in source


def test_findings_store_keeps_full_evidence():
    r = _researcher(findings=list(_FINDINGS))
    assert r.findings[0]["evidence"].startswith("Long quote")


def test_stop_when_coverage_complete():
    r = _researcher()
    covered = json.dumps({
        "sub_topics": [
            {
                "question": "Q1",
                "status": "covered",
                "key_facts": ["a [https://a.com]", "b [https://b.com]"],
                "gaps": [],
                "conflicts": [],
            },
        ],
        "insights": [],
    })
    assert r._scratchpad_coverage_ok(covered) is True


@pytest.mark.asyncio
async def test_retrieval_scale_settings():
    class _R(DeepResearcher):
        def __init__(self):
            super().__init__(
                llm_endpoint="http://local.test/v1/chat/completions",
                llm_model="local-model",
                max_urls_per_query=5,
                queries_round1=5,
            )

        async def _search(self, query):
            return [
                {"url": f"https://example.test/{query}/{i}", "title": f"{query}-{i}"}
                for i in range(10)
            ]

        async def _fetch_and_extract(self, url, question, title):
            return {"url": url, "title": title, "summary": "ok"}

    r = _R()
    import time
    r._start_time = time.time()
    queries = ["q1", "q2", "q3", "q4", "q5"]
    findings = await r._search_and_extract(queries, "question")
    assert len(findings) == 25


def test_citation_map_built():
    r = _researcher(findings=list(_FINDINGS))
    index, cmap = r._build_citation_index()
    assert "[^1]" in index
    assert "https://ex.com/a" in index
    assert len(cmap) == 2
    assert cmap[0]["id"] == 1


def test_progress_emit_includes_queries_and_insights():
    r = _researcher()
    events = []
    r._progress = events.append
    r._emit(
        phase="searching",
        queries=["q1", "q2"],
        insights=["insight"],
        step_description="Searching",
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["step"] == 1
    assert ev["queries"] == ["q1", "q2"]
    assert ev["insights"] == ["insight"]


@pytest.mark.asyncio
async def test_update_scratchpad_uses_180s_timeout():
    r = _researcher()
    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen.update(kwargs)
        return json.dumps({
            "sub_topics": [{"question": "Q", "status": "partial", "key_facts": [], "gaps": [], "conflicts": []}],
            "insights": ["found something"],
        })

    r._llm = _fake_llm
    r._emit = lambda **k: None
    out = await r._update_scratchpad("q", ["Q"], "", _FINDINGS)
    assert "sub_topics" in out
    assert seen.get("timeout", 0) >= 180


@pytest.mark.asyncio
async def test_compose_answer_uses_180s_timeout():
    r = _researcher(findings=list(_FINDINGS))
    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen.update(kwargs)
        return "Final answer [^1]"

    r._llm = _fake_llm
    out = await r._compose_answer("q", "{}", [])
    assert "Final answer" in out
    assert seen.get("timeout", 0) >= 180


def test_fallback_report_preserves_findings():
    r = _researcher()
    report = r._fallback_report("how does speaker diarization work", _FINDINGS)
    assert "speaker diarization" in report.lower()
    assert "Source A" in report
    assert "https://ex.com/a" in report
    assert "No information could be gathered" not in report


@pytest.mark.asyncio
async def test_scratchpad_failure_keeps_previous():
    r = _researcher()
    prev = json.dumps({"sub_topics": [], "insights": []})

    async def _boom(messages, **kwargs):
        raise RuntimeError("timeout")

    r._llm = _boom
    r._emit = lambda **k: None
    out = await r._update_scratchpad("q", [], prev, _FINDINGS)
    assert out == prev
