"""Разрешение API-ключа Cursor SDK."""

import os
import re
from pathlib import Path

_WORKER_ENV_KEY = re.compile(r"^\s*CURSOR_API_KEY\s*=\s*(.+?)\s*$")


class CursorSDKAuthError(RuntimeError):
    """Не удалось получить CURSOR_API_KEY."""


def _read_worker_env(path: Path) -> str:
    """Читает CURSOR_API_KEY из worker.env."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        match = _WORKER_ENV_KEY.match(line)
        if match:
            value = match.group(1).strip().strip('"').strip("'")
            return value
    return ""


def resolve_api_key(endpoint_key: str | None = None) -> str:
    """Возвращает API-ключ Cursor SDK.

    Порядок: явный ключ endpoint → env CURSOR_API_KEY → ~/.cursor/worker.env.

    Args:
        endpoint_key: Значение извести из ModelEndpoint или settings.

    Returns:
        Непустой API-ключ.

    Raises:
        CursorSDKAuthError: Если ключ не найден ни в одном источнике.
    """
    if endpoint_key and str(endpoint_key).strip():
        return str(endpoint_key).strip()

    env_key = os.getenv("CURSOR_API_KEY", "").strip()
    if env_key:
        return env_key

    worker_key = _read_worker_env(Path.home() / ".cursor" / "worker.env")
    if worker_key:
        return worker_key

    raise CursorSDKAuthError(
        "CURSOR_API_KEY не найден. Укажите ключ в Settings, переменной окружения "
        "или ~/.cursor/worker.env"
    )


def extract_api_key_from_headers(headers: dict | None) -> str | None:
    """Извлекает Cursor API key из internal headers endpoint resolver."""
    if not isinstance(headers, dict):
        return None
    auth = headers.get("Authorization") or ""
    if isinstance(auth, str) and auth.startswith("Bearer "):
        key = auth[7:].strip()
        if key:
            return key
    cursor_key = headers.get("X-Cursor-Api-Key")
    if cursor_key:
        return str(cursor_key).strip() or None
    return None
