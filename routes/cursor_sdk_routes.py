"""Cursor SDK probe и discovery routes."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import get_current_user
from src.cursor_sdk.auth import CursorSDKAuthError, resolve_api_key
from src.cursor_sdk.backend import CursorSDKBackend, CursorSDKError, CursorSDKRunError
from src.cursor_sdk.model_discovery import list_cursor_sdk_models_async
from src.cursor_sdk.provider import (
    CURSOR_SDK_BASE_URL,
    CURSOR_SDK_MODELS,
    build_cursor_sdk_base_url,
    normalize_cursor_sdk_model,
    resolve_cursor_sdk_cwd,
)
from src.settings import get_setting

logger = logging.getLogger(__name__)


class CursorSDKProbeRequest(BaseModel):
    model: str = Field(default="composer-2.5")
    workspace: str = Field(default="")
    api_key: str = Field(default="")


def _resolve_workspace(workspace: str) -> str:
    ws = (workspace or get_setting("cursor_workspace", "") or "").strip()
    base_url = build_cursor_sdk_base_url(ws) if ws else CURSOR_SDK_BASE_URL
    return resolve_cursor_sdk_cwd(base_url)


def setup_cursor_sdk_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/cursor-sdk/models")
    async def list_cursor_models(request: Request):
        """Список моделей Cursor SDK (live через async bridge)."""
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")

        api_key = resolve_api_key(
            (get_setting("cursor_api_key", "") or "").strip() or None
        )
        try:
            import cursor_sdk  # noqa: F401
        except ImportError:
            return {"models": list(CURSOR_SDK_MODELS), "source": "curated"}

        try:
            models = await list_cursor_sdk_models_async(
                api_key=api_key,
                base_url=CURSOR_SDK_BASE_URL,
            )
            if models:
                return {"models": models, "source": "sdk"}
        except CursorSDKAuthError as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            logger.warning("Cursor SDK async model list failed: %s", exc)
            raise HTTPException(503, f"Cursor SDK model discovery failed: {exc}") from exc

        return {"models": [], "source": "sdk"}

    @router.post("/api/cursor-sdk/probe")
    async def probe_cursor_sdk(body: CursorSDKProbeRequest, request: Request):
        """Проверяет подключение к Cursor SDK."""
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "Authentication required")

        api_key_override = (body.api_key or get_setting("cursor_api_key", "") or "").strip() or None
        try:
            model = normalize_cursor_sdk_model(body.model)
            backend = CursorSDKBackend(
                model=model,
                cwd=_resolve_workspace(body.workspace),
                api_key=api_key_override,
                scope="ephemeral",
            )
            await backend.probe(timeout=30)
        except CursorSDKAuthError as exc:
            raise HTTPException(503, str(exc)) from exc
        except CursorSDKError as exc:
            raise HTTPException(503, str(exc)) from exc
        except CursorSDKRunError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:
            logger.error("Cursor SDK probe unexpected error: %s", exc, exc_info=True)
            raise HTTPException(502, f"Cursor SDK probe failed: {exc}") from exc

        return {"ok": True, "model": model, "workspace": _resolve_workspace(body.workspace)}

    return router
