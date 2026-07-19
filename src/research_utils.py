# src/research_utils.py
"""Shared utilities for the deep research system.

Centralizes text cleaning, quality filtering, and other logic
used across deep_research.py, research_handler.py, and visual_report.py.
"""

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Thinking / reasoning block stripping
# ---------------------------------------------------------------------------

def strip_thinking(text):
    """Strip thinking / reasoning patterns from LLM output.

    Delegates to `src.text_helpers.strip_think` (single source of truth).
    Kept as an alias here so existing `from src.research_utils import strip_thinking`
    callers don't break. Preserves None passthrough — many callers pass an
    `Optional[str]` LLM result and expect None back when the call failed.
    """
    if text is None:
        return None
    from src.text_helpers import strip_think
    return strip_think(text, prose=False, prompt_echo=True)


# ---------------------------------------------------------------------------
# Source quality filtering
# ---------------------------------------------------------------------------

# Markers indicating extracted content is boilerplate, error text, or empty.
# If any marker is found (case-insensitive), the content is filtered out.
LOW_QUALITY_MARKERS = [
    "insufficient to",
    "content is insufficient",
    "no substantive data",
    "does not contain",
    "not relevant to",
    "no relevant information",
    "unable to extract",
    "completely unrelated",
    "boilerplate",
    "footer text",
    # Phrases (not bare "cookie"/"copyright") so we still catch boilerplate
    # like consent banners and footers without discarding legitimate findings
    # that merely discuss cookies or copyright as their subject.
    "cookie consent",
    "cookie banner",
    "cookie notice",
    "copyright notice",
    "copyright footer",
    "all rights reserved",
]


def is_low_quality(summary: str) -> bool:
    """Check if a finding summary indicates useless or irrelevant content."""
    try:
        if not isinstance(summary, str) or not summary:
            return True
        low = summary.lower()
        return any(marker in low for marker in LOW_QUALITY_MARKERS)
    except Exception:
        return False  # fail open


# ---------------------------------------------------------------------------
# URL normalization (cheap semantic-ish dedup)
# ---------------------------------------------------------------------------

_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
    "spm",
    "si",
})


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication across research rounds.

    Strips ``www.``, drops common tracking query params, sorts remaining
    query keys, and removes fragments. Returns the original string when
    parsing fails so callers can still use exact-string fallback.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw

    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_l = key.lower()
        if key_l in _TRACKING_QUERY_KEYS:
            continue
        if any(key_l.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))
    query_items.sort(key=lambda item: (item[0].lower(), item[1]))
    query = urlencode(query_items, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))
