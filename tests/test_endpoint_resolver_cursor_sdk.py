"""Тесты endpoint_resolver для cursor-sdk."""

from src.cursor_sdk.provider import CURSOR_SDK_BASE_URL
from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, normalize_base


def test_normalize_base_preserves_cursor_query():
    url = f"{CURSOR_SDK_BASE_URL}?cwd=/tmp/workspace"
    assert normalize_base(url) == url


def test_build_chat_url_cursor_sdk():
    assert build_chat_url(CURSOR_SDK_BASE_URL) == CURSOR_SDK_BASE_URL


def test_build_models_url_cursor_sdk():
    assert build_models_url(CURSOR_SDK_BASE_URL) is None


def test_build_headers_cursor_sdk(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "env_test_key")
    headers = build_headers(None, CURSOR_SDK_BASE_URL)
    assert headers.get("X-Cursor-Api-Key") == "env_test_key"
