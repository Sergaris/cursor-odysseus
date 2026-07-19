"""Regression tests for issue #1551 — deep research must not discard findings
when scratchpad/compose LLM calls fail or time out."""
import asyncio
import json

from src.deep_research import DeepResearcher


def _researcher():
    r = DeepResearcher.__new__(DeepResearcher)
    r.scratchpad_max_tokens = 4096
    r.max_report_tokens = 4096
    r.findings = []
    r.plan_sub_questions = []
    return r


_FINDINGS = [
    {"url": "https://ex.com/a", "title": "Diarization basics",
     "summary": "Speaker diarization segments audio by speaker identity."},
    {"url": "https://ex.com/b", "title": "x-vectors",
     "evidence": "x-vectors are embeddings used to cluster speech segments."},
]


def test_update_scratchpad_uses_generous_timeout():
    """Scratchpad update must get 180s like compose (#1551)."""
    r = _researcher()
    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen.update(kwargs)
        return json.dumps({"sub_topics": [], "insights": []})

    r._llm = _fake_llm
    r._emit = lambda **k: None

    asyncio.run(r._update_scratchpad("q", [], "", _FINDINGS))
    # Generous timeout: either explicit >=180s or None (unlimited) for local LLMs.
    timeout = seen.get("timeout", 0)
    assert timeout is None or timeout >= 180


def test_fallback_report_preserves_findings():
    """_fallback_report must surface gathered findings, not a give-up message."""
    r = _researcher()
    report = r._fallback_report("how does speaker diarization work", _FINDINGS)
    assert "speaker diarization" in report.lower()
    assert "Diarization basics" in report
    assert "x-vectors" in report
    assert "https://ex.com/a" in report
    assert "No information could be gathered" not in report


def test_scratchpad_failure_keeps_previous_notes():
    """If scratchpad update raises, previous scratchpad is preserved."""
    r = _researcher()

    async def _boom(messages, **kwargs):
        raise RuntimeError("502 after 3 attempts")

    r._llm = _boom
    r._emit = lambda **k: None

    prev = "existing scratchpad body"
    out = asyncio.run(r._update_scratchpad("q", [], prev, _FINDINGS))
    assert out == prev
