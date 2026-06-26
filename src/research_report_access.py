"""Подписанный доступ к HTML-отчёту deep research без cookie (новая вкладка / внешний браузер)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _secret() -> bytes:
    from core.middleware import INTERNAL_TOOL_TOKEN

    return INTERNAL_TOOL_TOKEN.encode("utf-8")


def sign_report_access(session_id: str, user: str, *, ttl_sec: int = 3600) -> str:
    """Создаёт query-token для GET /api/research/report/{session_id}?access=…"""
    exp = int(time.time()) + max(60, int(ttl_sec))
    payload: dict[str, Any] = {
        "sid": session_id,
        "user": user,
        "exp": exp,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + b"." + sig).decode("ascii").rstrip("=")


def verify_report_access(session_id: str, token: str) -> str | None:
    """Проверяет token и возвращает username или None."""
    if not session_id or not token:
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        body, sig = raw.rsplit(b".", 1)
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(body.decode("utf-8"))
        if payload.get("sid") != session_id:
            return None
        exp = int(payload.get("exp") or 0)
        if exp < int(time.time()):
            return None
        user = str(payload.get("user") or "").strip()
        return user or None
    except Exception:
        return None
