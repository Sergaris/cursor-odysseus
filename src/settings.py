# src/settings.py
"""Centralized settings and features management.

Single source of truth for reading/writing data/settings.json and data/features.json.
All modules should import from here instead of accessing files directly.
"""

import json
import time
import logging
from typing import Any

from src.constants import SETTINGS_FILE, FEATURES_FILE

logger = logging.getLogger(__name__)

# Tiny TTL cache for settings/features. get_setting() is called on hot paths
# (every chat, every preprocess); without this it re-parses the JSON each call.
# Picks up edits within _CACHE_TTL seconds, which is fine for human-edited config.
_CACHE_TTL = 2.0
_settings_cache: tuple[float, dict] | None = None
_features_cache: tuple[float, dict] | None = None

def _invalidate_caches():
    global _settings_cache, _features_cache
    _settings_cache = None
    _features_cache = None

# ── Default values ──

DEFAULT_SETTINGS = {
    # Agent email safety: when True, the MCP send_email / reply_to_email
    # tools don't SMTP directly. They stage the composed message into the
    # scheduled_emails table with status='agent_draft' and return a
    # pending_id + the rendered email so the user can review and approve
    # (or cancel) before it actually goes out. Default ON because models
    # have been observed inventing signatures and sending to real
    # recipients without confirmation.
    "agent_email_confirm": True,
    "image_gen_enabled": False,
    "image_model": "",
    "image_quality": "medium",
    "vision_model": "",
    "vision_enabled": True,
    # Ordered fallback chain for the Vision model (image analysis, OCR, tagging).
    "vision_model_fallbacks": [],
    # Public base URL used to build clickable deep-links in outgoing alerts
    # (e.g., urgency alert email). Example: "https://chat.example.com"
    "app_public_url": "",
    "tts_enabled": True,
    "tts_provider": "disabled",
    "tts_model": "tts-1",
    "tts_voice": "alloy",
    "tts_speed": "1",
    "stt_enabled": False,
    "stt_provider": "disabled",
    "stt_model": "base",
    "stt_language": "",
    "search_provider": "searxng",
    # Default fallback chain — when the primary provider fails or
    # rate-limits, we try DuckDuckGo next. Free, no API key required, so
    # safe to ship on by default for every user.
    "search_fallback_chain": ["duckduckgo"],
    "search_url": "",
    "search_result_count": 5,
    # SafeSearch level applied to every provider that exposes one.
    # "strict"   — block adult / explicit results (default; matches what users
    #              expect from a research tool and avoids unrelated NSFW URLs
    #              bleeding in via provider "related" / spam recommendations)
    # "moderate" — provider-default behavior (filter explicit but allow
    #              suggestive content)
    # "off"      — disable filtering entirely (advanced users only)
    #
    # Providers that honor this setting (translated to each provider's native
    # param in src/search/providers.py:_safesearch_for):
    #     SearXNG       safesearch=0/1/2 (JSON API, HTML scrape, news fallback)
    #     Brave Search  safesearch=off/moderate/strict
    #     DuckDuckGo    safesearch=off/moderate/on (library + HTML kp param)
    #     Google PSE    safe=active (omitted for "off"; PSE has no middle tier)
    #     Serper.dev    safe=active (omitted for "off"; proxies Google's `safe`)
    # Providers NOT touched: Tavily (no SafeSearch knob; filters at index time)
    # and any custom backend reached via search_url — they keep whatever the
    # backend itself decides, so operators stay in control of self-hosted /
    # niche search instances.
    "search_safesearch": "strict",
    "brave_api_key": "",
    "google_pse_key": "",
    "google_pse_cx": "",
    "tavily_api_key": "",
    "serper_api_key": "",
    "research_endpoint_id": "",
    "research_model": "",
    # Deep Research brain: "http" (OpenAI-compat endpoint) or "cursor_sdk".
    "research_brain_provider": "http",
    "research_brain_model": "composer-2.5",
    # Абсолютный путь workspace для Cursor SDK local agent; пусто = корень приложения.
    "cursor_workspace": "",
    # Опциональный override API-ключа; иначе env CURSOR_API_KEY или ~/.cursor/worker.env.
    "cursor_api_key": "",
    # Hybrid mode: при research_brain_provider=cursor_sdk per-URL extract через HTTP.
    "research_extract_endpoint_id": "",
    "research_extract_model": "",
    "research_search_provider": "",
    "research_max_tokens": 16384,
    # Per-LLM-call timeouts for Deep Research. 0 = no read/inference cap.
    "research_extraction_timeout_seconds": 0,
    "research_planning_timeout_seconds": 0,
    "research_query_timeout_seconds": 0,
    "research_heavy_llm_timeout_seconds": 0,
    "research_extraction_concurrency": 8,
    # Per-run budget inside DeepResearcher (rounds stop when exceeded). 0 = unlimited.
    "research_max_time_seconds": 0,
    "research_queries_round1": 5,
    "research_queries_followup": 4,
    "research_max_urls_per_query": 5,
    # Hard wall-clock cap on a single deep-research run. 0 = no limit (asyncio
    # wait_for disabled). Any other value is bounded to [60, 86400].
    "research_run_timeout_seconds": 0,
    # Query-aware page compression before LLM extract: heuristic | provence | off.
    "research_compression_backend": "heuristic",
    # Embedding coverage score threshold for early stop (0..1). 0 disables.
    "research_coverage_threshold": 0.75,
    "agent_max_tool_calls": 0,
    "agent_max_rounds": 20,  # per-message agent step cap (clamped 1..200)
    # Soft input-token budget for the agent loop. The DEFAULT value (6000) is the
    # "auto" sentinel: it means "scale the budget to the model's context window"
    # (#1230) — so long-context models aren't capped at 6000. Set ANY OTHER value
    # to enforce an explicit cap (clamped to the window only — hard_max does not
    # apply to explicit budgets, #1230); set 0 to disable soft-trimming. The
    # default is treated as auto because the settings-save path materializes
    # defaults, so a persisted 6000 can't be told apart from a deliberate 6000 —
    # to pin a budget near the default, use a nearby value (e.g. 5999).
    "agent_input_token_budget": 6000,
    # Ceiling on the *auto-derived* input budget; a configurable setting since #1273
    # (the merged #1230 left it a module constant). No effect on an explicit budget
    # — a deliberate value is honoured (#1230). Default matches
    # `src.context_budget.DEFAULT_HARD_MAX`; lower this for
    # cost-paranoid setups, raise it on premium APIs with very large windows you
    # want to actually use (e.g. 900_000 to fill a 1M-context model). See
    # `compute_input_token_budget`.
    "agent_input_token_hard_max": 200_000,
    "agent_stream_timeout_seconds": 900,
    # Extra directory roots that read_file / write_file may access, in
    # addition to the built-in project data/ and system temp dirs. Each
    # entry is an absolute path. Sensitive subpaths (.ssh, .gnupg, shell
    # rc files, SSH key files) are always blocked regardless of roots.
    "tool_path_extra_roots": [],
    "task_endpoint_id": "",
    "task_model": "",
    "default_endpoint_id": "",
    "default_model": "",
    # Ordered fallback chain for the default chat model. Each entry is
    # {"endpoint_id": "...", "model": "..."}. If the primary model fails
    # before producing output (endpoint offline / errors), the chat
    # dispatch retries the next entry in order.
    "default_model_fallbacks": [],
    # When True, non-admin users inherit global default model/endpoint/fallbacks
    # when they have no personal defaults. When False, users only use their
    # personal defaults (no global fallback). Default is False.
    "share_defaults_with_users": False,
    "utility_endpoint_id": "",
    "utility_model": "",
    # Ordered fallback chain for the Utility model (summarization, naming,
    # tidy actions, etc.).
    "utility_model_fallbacks": [],
    "teacher_model": "",
    "teacher_enabled": False,
    # Skills: minimum self-reported confidence for an auto-written (LLM-authored)
    # DRAFT skill to be injected into the agent prompt. Published skills always
    # qualify. Keeps low-confidence auto-skills out of context until they're
    # vetted/published. 0 disables the gate.
    "skill_autosave_min_confidence": 0.85,
    # Max relevant skills injected into the prompt for one request. The skills
    # library can grow beyond this; cleanup/retirement is an explicit review flow.
    "skill_max_injected": 3,
    # Reminders
    "reminder_channel": "browser",   # "browser" | "email" | "ntfy" | "webhook"
    "reminder_llm_synthesis": False,
    "reminder_llm_persona": "",
    "reminder_ntfy_topic": "Reminders",
    "reminder_email_to": "",
    # Generic outbound webhook channel: pick any saved Integration as the
    # target and supply a JSON payload template. Use {{title}} and {{message}}
    # as placeholders — they are JSON-escaped before substitution, so the
    # rendered string is always valid JSON. Works with Discord, Slack, Teams,
    # ntfy (JSON mode), or any service that accepts a POST with a JSON body.
    "reminder_webhook_integration_id": "",
    "reminder_webhook_payload_template": "",
    # Email triage scanner rules. Running/paused state and schedule live in
    # Tasks via the built-in `check_email_urgency` task.
    "urgent_email_prompt": (
        "Flag as urgent: explicit deadlines, time-sensitive requests, "
        "work-blocking issues, messages from people I report to, or anything "
        "where a delayed reply costs money/trust. Someone waiting outside, "
        "at the door, locked out, or unable to get in is urgent now. "
        "Newsletters, marketing, automated digests, and FYI-only updates are "
        "NOT urgent."
    ),
    # Keyboard shortcuts (action: key combination)
    "keybinds": {
        "search": "ctrl+k",
        "toggle_sidebar": "ctrl+b",
        "new_session": "ctrl+alt+n",
        "star_session": "ctrl+alt+s",
        "delete_session": "ctrl+alt+d",
        "admin_panel": "ctrl+shift+u",
        "cancel": "escape",
    },
}

DEFAULT_FEATURES = {
    "web_search": True,
    "web_fetch": True,
    "deep_research": False,
    "memory": True,
    "document_editor": True,
    "rag": True,
    "sensitive_filter": True,
    "gallery": True,
}


# ── Settings (data/settings.json) ──

_PORTABLE_FALLBACK_PROVIDER = "duckduckgo"


def _is_local_host_url(url: str) -> bool:
    """True if URL points at this machine (default SearXNG docker target)."""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


def sync_portable_search_at_startup() -> None:
    """На старте portable exe: дождаться/повторить SearXNG и обновить кэш настроек."""
    from src.frozen_runtime import is_frozen

    if not is_frozen():
        return
    from src.bundled_searxng import (
        get_bundled_searxng_url,
        is_bundled_probe_finished,
        retry_bundled_searxng_startup,
        schedule_bundled_searxng_background,
    )

    if get_bundled_searxng_url():
        _invalidate_caches()
        return

    if is_bundled_probe_finished():
        url = retry_bundled_searxng_startup()
        if url:
            persist_portable_search_defaults()
        else:
            persist_portable_search_defaults(fallback_only=True)
        _invalidate_caches()
        return

    schedule_bundled_searxng_background()


def _apply_portable_search_overlay(settings: dict) -> dict:
    """Portable: bundled SearXNG URL или DuckDuckGo fallback."""
    from src.bundled_searxng import (
        get_bundled_searxng_url,
        is_bundled_probe_finished,
        resolve_bundled_searxng_url,
    )
    from src.frozen_runtime import is_frozen

    if not is_frozen():
        return settings

    bundled_url = get_bundled_searxng_url()
    if not bundled_url and is_bundled_probe_finished():
        bundled_url = resolve_bundled_searxng_url(adopt=True)
    if bundled_url:
        patched = {
            **settings,
            "search_provider": "searxng",
            "search_url": bundled_url,
        }
        research_sp = (patched.get("research_search_provider") or "").strip()
        if research_sp == "searxng" or not research_sp:
            patched["research_search_provider"] = ""
        return patched

    url = (settings.get("search_url") or "").strip()
    if url and not _is_local_host_url(url):
        return settings
    provider = (settings.get("search_provider") or "searxng").strip()
    if provider != "searxng" and not url:
        return settings
    patched = {
        **settings,
        "search_provider": _PORTABLE_FALLBACK_PROVIDER,
        "search_url": "",
    }
    research_sp = (patched.get("research_search_provider") or "").strip()
    if research_sp == "searxng":
        patched["research_search_provider"] = ""
    return patched


def persist_portable_search_defaults(*, fallback_only: bool = False) -> None:
    """Записывает search_provider/search_url для portable (SearXNG или fallback)."""
    from pathlib import Path

    from src.bundled_searxng import (
        get_bundled_searxng_url,
        is_bundled_probe_finished,
        resolve_bundled_searxng_url,
    )
    from src.frozen_runtime import is_frozen

    if not is_frozen():
        return
    path = Path(SETTINGS_FILE)
    try:
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
        else:
            saved = {}
    except (json.JSONDecodeError, OSError):
        saved = {}

    bundled_url = get_bundled_searxng_url()
    if not bundled_url and is_bundled_probe_finished():
        bundled_url = resolve_bundled_searxng_url(adopt=True)
    if bundled_url:
        desired_provider = "searxng"
        desired_url = bundled_url
    else:
        merged = _apply_portable_search_overlay({**DEFAULT_SETTINGS, **saved})
        desired_provider = merged.get("search_provider", "searxng")
        desired_url = merged.get("search_url") or ""

    if saved.get("search_provider") == desired_provider and (saved.get("search_url") or "") == desired_url:
        return

    to_save = {**saved}
    to_save["search_provider"] = desired_provider
    to_save["search_url"] = desired_url
    if bundled_url:
        to_save["research_search_provider"] = ""
    elif (saved.get("research_search_provider") or "").strip() == "searxng":
        to_save["research_search_provider"] = ""
    save_settings(to_save)


def load_settings() -> dict:
    """Load settings merged with defaults. Always returns a complete dict."""
    global _settings_cache
    now = time.monotonic()
    if _settings_cache and (now - _settings_cache[0]) < _CACHE_TTL:
        return _settings_cache[1]
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("settings must be an object")
        merged = {**DEFAULT_SETTINGS, **saved}
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_SETTINGS)
    merged = _apply_portable_search_overlay(merged)
    _settings_cache = (now, merged)
    return merged


def save_settings(settings: dict):
    """Persist settings to disk (atomic; see core.atomic_io)."""
    from core.atomic_io import atomic_write_json
    atomic_write_json(SETTINGS_FILE, settings, indent=2)
    _invalidate_caches()


def get_setting(key: str, default: Any = None) -> Any:
    """Read a single setting value."""
    return load_settings().get(key, default)


def is_setting_overridden(key: str) -> bool:
    """True if ``key`` is explicitly present in the saved settings file.

    ``load_settings`` merges DEFAULT_SETTINGS with the saved file, so a value
    equal to its default is indistinguishable from "never set" via get_setting.
    Callers that must distinguish an explicit user choice from a default read
    the raw saved file via this. (Note: a materialized default is also "present",
    so value-sensitive callers should compare against the default — see
    ``context_budget.budget_is_explicit``.)
    """
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        return isinstance(saved, dict) and key in saved
    except (FileNotFoundError, json.JSONDecodeError):
        return False


# Per-user settings (user prefs override the global admin default). Used for
# keys that a user is allowed to choose individually — currently the vision
# model + image-generation model. The owner argument is the authed username
# resolved by FastAPI deps; an empty/None owner falls through to the global.
_PER_USER_KEYS = {
    "vision_model", "vision_enabled", "vision_model_fallbacks",
    "image_model", "image_gen_enabled", "image_quality",
    # Default chat endpoint / model — without per-user resolution every new
    # account inherited whatever the most-recent admin picked, which then
    # got injected into the chat composer on first open.
    "default_endpoint_id", "default_model", "default_model_fallbacks",
    "utility_endpoint_id", "utility_model", "utility_model_fallbacks",
    "research_endpoint_id", "research_model",
}


def get_user_setting(key: str, owner: str = "", default: Any = None) -> Any:
    """Resolve `key` from the caller's per-user prefs first, falling back to
    the global setting. Only the small whitelist in `_PER_USER_KEYS` is
    eligible — for any other key this is equivalent to `get_setting(key)`.

    Falls back gracefully if the prefs module can't be imported (cycle/early
    boot) — admin-global settings keep working.
    """
    if owner and key in _PER_USER_KEYS:
        try:
            from routes.prefs_routes import _load_for_user
            prefs = _load_for_user(owner) or {}
            if key in prefs and prefs[key] not in (None, ""):
                return prefs[key]
        except Exception:
            pass
    return get_setting(key, default)


# ── Features (data/features.json) ──

def load_features() -> dict:
    """Load feature flags merged with defaults."""
    global _features_cache
    now = time.monotonic()
    if _features_cache and (now - _features_cache[0]) < _CACHE_TTL:
        return _features_cache[1]
    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("features must be an object")
        merged = {**DEFAULT_FEATURES, **saved}
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, ValueError):
        merged = dict(DEFAULT_FEATURES)
    _features_cache = (now, merged)
    return merged


def save_features(features: dict):
    """Persist feature flags to disk (atomic)."""
    from core.atomic_io import atomic_write_json
    atomic_write_json(FEATURES_FILE, features, indent=2)
    _invalidate_caches()
