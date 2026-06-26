"""Тесты resolve_api_key для Cursor SDK."""

import os
from pathlib import Path

import pytest

from src.cursor_sdk.auth import CursorSDKAuthError, resolve_api_key


def test_resolve_api_key_from_endpoint_override(monkeypatch, tmp_path):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    assert resolve_api_key("  cursor_test_key  ") == "cursor_test_key"


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "env_key_123")
    assert resolve_api_key(None) == "env_key_123"


def test_resolve_api_key_from_worker_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    worker_env = cursor_dir / "worker.env"
    worker_env.write_text('CURSOR_API_KEY="worker_key_456"\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_api_key(None) == "worker_key_456"


def test_extract_api_key_from_headers_cursor_header():
    from src.cursor_sdk.auth import extract_api_key_from_headers

    assert extract_api_key_from_headers({"X-Cursor-Api-Key": "abc"}) == "abc"


def test_extract_api_key_from_headers_bearer():
    from src.cursor_sdk.auth import extract_api_key_from_headers

    assert extract_api_key_from_headers({"Authorization": "Bearer tok"}) == "tok"


def test_resolve_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(CursorSDKAuthError):
        resolve_api_key(None)
