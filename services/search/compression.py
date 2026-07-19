"""Query-aware content compression for Deep Research extraction.

Backends:
- ``heuristic`` — keyword density + paragraph scoring (default, no extra deps)
- ``provence`` — OpenProvence / Provence if installed
- ``off`` — passthrough (caller may still truncate)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9а-яё]{3,}", re.IGNORECASE)
_BOILERPLATE_MARKERS = (
    "cookie",
    "privacy policy",
    "terms of service",
    "subscribe",
    "newsletter",
    "all rights reserved",
    "sign in",
    "log in",
    "advertisement",
    "related articles",
)


def compress_content(
    content: str,
    query: str,
    *,
    backend: str = "heuristic",
    max_chars: int = 15000,
) -> str:
    """Compress page text for a research query.

    Args:
        content: Raw page text.
        query: Research question / goal.
        backend: ``heuristic``, ``provence``, or ``off``.
        max_chars: Soft ceiling after compression.

    Returns:
        Compressed text (never empty if input was non-empty).
    """
    text = (content or "").strip()
    if not text:
        return ""

    mode = (backend or "heuristic").strip().lower()
    if mode == "off":
        return text if len(text) <= max_chars else text[:max_chars]

    if mode == "provence":
        compressed = _compress_provence(text, query, max_chars=max_chars)
        if compressed:
            return compressed
        logger.info("Provence unavailable; falling back to heuristic compression")

    return _compress_heuristic(text, query, max_chars=max_chars)


def _compress_provence(content: str, query: str, *, max_chars: int) -> str:
    """Try OpenProvence / Provence; return empty string on failure."""
    try:
        # open_provence public API varies by version; try common entry points.
        try:
            from open_provence import ProvencePruner  # type: ignore
            pruner = ProvencePruner()
            result = pruner.prune(query=query, context=content)
            if isinstance(result, str) and result.strip():
                return result.strip()[:max_chars]
            if isinstance(result, dict):
                text = result.get("text") or result.get("context") or ""
                if text:
                    return str(text).strip()[:max_chars]
        except ImportError:
            pass

        try:
            from open_provence import prune_context  # type: ignore
            result = prune_context(query=query, context=content)
            if isinstance(result, str) and result.strip():
                return result.strip()[:max_chars]
        except ImportError:
            pass

        try:
            import open_provence  # type: ignore
            if hasattr(open_provence, "compress"):
                result = open_provence.compress(query, content)
                if isinstance(result, str) and result.strip():
                    return result.strip()[:max_chars]
        except ImportError:
            return ""
    except Exception as exc:
        logger.warning("Provence compression failed: %s", exc)
        return ""
    return ""


def _compress_heuristic(content: str, query: str, *, max_chars: int) -> str:
    """Keep paragraphs that look query-relevant; drop boilerplate."""
    query_terms = set(_WORD_RE.findall((query or "").lower()))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    if not paragraphs:
        return content[:max_chars]

    scored: list[tuple[float, str]] = []
    for para in paragraphs:
        low = para.lower()
        if any(marker in low for marker in _BOILERPLATE_MARKERS) and len(para) < 400:
            continue
        words = set(_WORD_RE.findall(low))
        if not words:
            continue
        overlap = len(words & query_terms) / max(1, len(query_terms)) if query_terms else 0.0
        length_bonus = min(len(para), 1200) / 1200
        score = (2.0 * overlap) + (0.35 * length_bonus)
        if overlap == 0 and len(para) < 120:
            continue
        scored.append((score, para))

    if not scored:
        return content[:max_chars]

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[str] = []
    total = 0
    # Keep original order among selected paragraphs for readability.
    keep = {para for _, para in scored[: max(6, len(scored) // 2)]}
    for para in paragraphs:
        if para not in keep:
            continue
        if total + len(para) + 2 > max_chars:
            break
        selected.append(para)
        total += len(para) + 2

    if not selected:
        return scored[0][1][:max_chars]
    return "\n\n".join(selected)


def keyword_density(text: str, terms: Iterable[str]) -> float:
    """Fraction of query terms present in text (helper for tests)."""
    words = set(_WORD_RE.findall((text or "").lower()))
    term_set = {t.lower() for t in terms if t}
    if not term_set:
        return 0.0
    return len(words & term_set) / len(term_set)
