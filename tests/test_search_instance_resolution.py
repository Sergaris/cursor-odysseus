"""Тесты выбора URL SearXNG для поиска."""

from services.search import providers


def test_get_search_instance_prefers_bundled_over_empty(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {"search_provider": "searxng", "search_url": ""},
    )
    monkeypatch.setattr(
        "src.bundled_searxng.resolve_bundled_searxng_url",
        lambda **_k: "http://127.0.0.1:8082",
    )
    assert providers._get_search_instance() == "http://127.0.0.1:8082"


def test_get_search_instance_prefers_bundled_over_stale_localhost(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {"search_provider": "searxng", "search_url": "http://localhost:8080"},
    )
    monkeypatch.setattr(
        "src.bundled_searxng.resolve_bundled_searxng_url",
        lambda **_k: "http://127.0.0.1:8082",
    )
    assert providers._get_search_instance() == "http://127.0.0.1:8082"


def test_get_search_instance_keeps_remote_url(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_get_search_settings",
        lambda: {
            "search_provider": "searxng",
            "search_url": "https://search.example.com",
        },
    )
    monkeypatch.setattr(
        "src.bundled_searxng.resolve_bundled_searxng_url",
        lambda **_k: "http://127.0.0.1:8082",
    )
    assert providers._get_search_instance() == "https://search.example.com"
