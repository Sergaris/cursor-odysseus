"""Disk serde for per-round scratchpads."""

from pathlib import Path

from src.deep_research import DeepResearcher


def test_persist_and_load_scratchpad(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DEEP_RESEARCH_DIR", str(tmp_path))
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        session_id="sess-serde-1",
    )
    payload = '{"sub_topics": [], "insights": ["a"]}'
    researcher._persist_scratchpad(1, payload)
    researcher._persist_scratchpad(2, '{"insights": ["b"]}')

    state_dir = Path(tmp_path) / "sess-serde-1"
    assert (state_dir / "scratchpad_r1.json").exists()
    assert researcher._load_latest_scratchpad() == '{"insights": ["b"]}'


def test_persist_noop_without_session(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DEEP_RESEARCH_DIR", str(tmp_path))
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        session_id="",
    )
    researcher._persist_scratchpad(1, "{}")
    assert list(Path(tmp_path).glob("**/scratchpad_r*.json")) == []
