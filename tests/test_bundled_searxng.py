"""Тесты bundled SearXNG (SimpleXNG sidecar)."""

import json
from unittest import mock

from src import settings
from src.bundled_searxng import (
    BUNDLED_SEARXNG_URL_ENV,
    _apply_portable_simplexng_overlays,
    _is_port_free,
    _pick_port,
    _searxng_healthy,
    discover_running_searxng_url,
    ensure_limiter_toml_file,
    get_bundled_searxng_url,
    mark_bundled_probe_finished,
    reset_bundled_probe_state,
    resolve_bundled_searxng_url,
    start_bundled_searxng,
    stop_bundled_searxng,
)


def test_is_port_free_localhost():
    port = 37652
    if not _is_port_free("127.0.0.1", port):
        port = 37653
    assert _is_port_free("127.0.0.1", port) or _is_port_free("127.0.0.1", 37653)


def test_pick_port_returns_candidate(monkeypatch):
    monkeypatch.setattr("src.bundled_searxng._port_has_listener", lambda _h, _p: False)
    monkeypatch.setattr("src.bundled_searxng._is_port_free", lambda _h, p: p == 8888)
    monkeypatch.setattr("src.bundled_searxng._searxng_healthy", lambda *_a, **_k: False)
    assert _pick_port("127.0.0.1") == 8888


def test_pick_port_skips_occupied_non_searxng(monkeypatch):
    monkeypatch.setattr("src.bundled_searxng._searxng_healthy", lambda *_a, **_k: False)
    monkeypatch.setattr("src.bundled_searxng._port_has_listener", lambda _h, p: p == 8888)
    monkeypatch.setattr("src.bundled_searxng._is_port_free", lambda _h, p: p == 8081)
    assert _pick_port("127.0.0.1") == 8081


def test_get_bundled_url_from_env(monkeypatch):
    monkeypatch.setenv(BUNDLED_SEARXNG_URL_ENV, "http://127.0.0.1:8888")
    assert get_bundled_searxng_url() == "http://127.0.0.1:8888"


def test_portable_overlay_uses_bundled_searxng(monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: True)
    monkeypatch.setenv("ODYSSEUS_BUNDLED_SEARXNG_URL", "http://127.0.0.1:8888")
    settings._invalidate_caches()
    merged = settings._apply_portable_search_overlay(
        {**settings.DEFAULT_SETTINGS, "search_provider": "duckduckgo", "search_url": ""}
    )
    assert merged["search_provider"] == "searxng"
    assert merged["search_url"] == "http://127.0.0.1:8888"


def test_portable_overlay_fallback_without_bundled(monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: True)
    monkeypatch.setattr("src.bundled_searxng.resolve_bundled_searxng_url", lambda **_k: "")
    settings._invalidate_caches()
    merged = settings._apply_portable_search_overlay(
        {**settings.DEFAULT_SETTINGS, "search_provider": "searxng", "search_url": ""}
    )
    assert merged["search_provider"] == "duckduckgo"


def test_persist_portable_writes_bundled_searxng(tmp_path, monkeypatch):
    monkeypatch.setattr("src.frozen_runtime.is_frozen", lambda: True)
    monkeypatch.setenv("ODYSSEUS_BUNDLED_SEARXNG_URL", "http://127.0.0.1:8888")
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"search_provider": "duckduckgo"}), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()
    settings.persist_portable_search_defaults()
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["search_provider"] == "searxng"
    assert saved["search_url"] == "http://127.0.0.1:8888"


def test_start_bundled_skips_when_not_enabled(monkeypatch):
    monkeypatch.setattr("src.bundled_searxng.bundled_searxng_enabled", lambda: False)
    assert start_bundled_searxng() is False


def test_stop_bundled_is_idempotent():
    stop_bundled_searxng()
    stop_bundled_searxng()


def test_searxng_healthy_mock():
    class _Resp:
        status_code = 200
        text = '{"results": []}'

    with mock.patch("src.bundled_searxng.httpx.get", return_value=_Resp()):
        assert _searxng_healthy("http://127.0.0.1:8888") is True


def test_discover_running_searxng_url(monkeypatch):
    monkeypatch.setattr("src.bundled_searxng._port_has_listener", lambda _h, p: p == 8082)
    monkeypatch.setattr(
        "src.bundled_searxng._searxng_healthy",
        lambda url, **_k: url.endswith(":8082"),
    )
    assert discover_running_searxng_url() == "http://127.0.0.1:8082"


def test_discover_skips_ports_without_listener(monkeypatch):
    calls: list[str] = []

    def _healthy(url: str, **_k: object) -> bool:
        calls.append(url)
        return False

    monkeypatch.setattr("src.bundled_searxng._port_has_listener", lambda _h, _p: False)
    monkeypatch.setattr("src.bundled_searxng._searxng_healthy", _healthy)
    assert discover_running_searxng_url() == ""
    assert calls == []


def test_resolve_skips_discovery_before_probe(monkeypatch):
    reset_bundled_probe_state()
    monkeypatch.setenv(BUNDLED_SEARXNG_URL_ENV, "")
    monkeypatch.setattr(
        "src.bundled_searxng.discover_running_searxng_url",
        lambda: (_ for _ in ()).throw(AssertionError("must not scan ports")),
    )
    assert resolve_bundled_searxng_url() == ""


def test_resolve_adopts_discovered_url(monkeypatch):
    reset_bundled_probe_state()
    mark_bundled_probe_finished("")
    monkeypatch.setenv(BUNDLED_SEARXNG_URL_ENV, "")
    monkeypatch.setattr("src.bundled_searxng._instance_url", "", raising=False)
    monkeypatch.setattr("src.bundled_searxng._port_has_listener", lambda _h, p: p == 8082)
    monkeypatch.setattr(
        "src.bundled_searxng._searxng_healthy",
        lambda url, **_k: url.endswith(":8082"),
    )
    assert resolve_bundled_searxng_url() == "http://127.0.0.1:8082"
    assert get_bundled_searxng_url() == "http://127.0.0.1:8082"


def test_portable_simplexng_overlay_limits_engines():
    merged = _apply_portable_simplexng_overlays({})
    assert merged["use_default_settings"]["engines"]["keep_only"] == [
        "duckduckgo",
        "wikipedia",
        "brave",
    ]
    assert merged["checker"]["scheduling"] == {}
    assert merged["server"]["limiter"] is False


def test_ensure_limiter_toml_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr("src.bundled_searxng.get_default_data_dir", lambda: str(tmp_path))
    path = ensure_limiter_toml_file()
    assert path.is_file()
    assert path.name == "limiter.toml"
    assert ensure_limiter_toml_file() == path
