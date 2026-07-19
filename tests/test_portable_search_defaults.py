"""Portable exe search defaults with bundled SearXNG."""

import json

from src import settings


def test_portable_overlay_keeps_remote_searxng(monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: True)
    monkeypatch.setattr("src.bundled_searxng.resolve_bundled_searxng_url", lambda **_k: "")
    settings._invalidate_caches()
    merged = settings._apply_portable_search_overlay(
        {
            **settings.DEFAULT_SETTINGS,
            "search_provider": "searxng",
            "search_url": "https://search.example.com",
        }
    )
    assert merged["search_provider"] == "searxng"
    assert merged["search_url"] == "https://search.example.com"


def test_portable_overlay_ignored_when_not_frozen(monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: False)
    settings._invalidate_caches()
    merged = settings._apply_portable_search_overlay(
        {**settings.DEFAULT_SETTINGS, "search_provider": "searxng", "search_url": ""}
    )
    assert merged["search_provider"] == "searxng"


def test_persist_portable_search_defaults_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: True)
    monkeypatch.setattr("src.bundled_searxng.resolve_bundled_searxng_url", lambda **_k: "")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"search_provider": "searxng", "search_url": "http://localhost:8081"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()
    settings.persist_portable_search_defaults(fallback_only=True)
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["search_provider"] == "duckduckgo"
    assert saved["search_url"] == ""
