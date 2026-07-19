"""Deprecated: runtime limit tuning removed.

Deep Research no longer adjusts timeouts or concurrency by endpoint profile.
Values pass through unchanged for any legacy imports.
"""

from dataclasses import dataclass
from typing import Literal

ResearchProfile = Literal["local", "remote", "cursor_sdk"]


@dataclass(frozen=True, slots=True)
class ResearchLimits:
    """Effective timeouts and concurrency for a research run."""

    profile: ResearchProfile
    extraction_timeout: int | None
    planning_timeout: int | None
    query_timeout: int | None
    extraction_concurrency: int
    probe_chat_timeout: int = 0


def infer_research_profile(url: str, model: str = "") -> ResearchProfile:
    """Classify endpoint type (informational only)."""
    from urllib.parse import urlparse

    from src.cursor_sdk.provider import is_cursor_sdk_base

    if is_cursor_sdk_base(url or ""):
        return "cursor_sdk"
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}:
        return "local"
    return "remote"


def apply_research_limits(
    profile: ResearchProfile,
    *,
    extraction_timeout: int | None,
    planning_timeout: int | None,
    query_timeout: int | None,
    extraction_concurrency: int,
    local_min_timeout: int = 0,
) -> ResearchLimits:
    """Passthrough — limits are no longer adjusted at runtime."""
    return ResearchLimits(
        profile=profile,
        extraction_timeout=extraction_timeout,
        planning_timeout=planning_timeout,
        query_timeout=query_timeout,
        extraction_concurrency=extraction_concurrency,
    )


def log_research_limit_adjustments(*args, **kwargs) -> None:
    """No-op — runtime tuning removed."""
