"""Search result ranking based on relevance, source quality, and recency."""

import re
import logging
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_AGE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _utcnow_naive() -> datetime:
    """Naive UTC 'now'. Matches the naive, UTC-style published dates parsed below,
    and is safe on Python 3.14 where ``datetime.utcnow()`` is removed (#1116)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def recency_score(age_str: Optional[str], now: Optional[datetime] = None) -> float:
    """Score how recent a result is: 1.0 for <=7 days old, 0.0 for >=30 days.

    The age is measured against UTC, not local time. The previous code used
    ``datetime.now()`` (local) against UTC-style published dates, so the age was
    skewed by the host's UTC offset; it was also a latent crash once neighbouring
    code moves to timezone-aware datetimes (#1116). ``now`` is injectable for tests.
    """
    if not age_str:
        return 0.0
    dt = None
    for fmt in _AGE_FORMATS:
        try:
            dt = datetime.strptime(age_str, fmt)
            break
        except Exception:
            dt = None
    if not dt:
        return 0.0
    now = now or _utcnow_naive()
    days_old = (now - dt).days
    if days_old <= 7:
        return 1.0
    if days_old >= 30:
        return 0.0
    return (30 - days_old) / 23


_NEWS_HINTS = {"news", "nyheter", "headlines", "breaking", "latest", "today", "idag"}
_SPORTS_HINTS = {
    "sport", "sports", "soccer", "football", "hockey", "nba", "nfl", "mlb",
    "fifa", "world cup", "championship", "quarterfinal", "eliminates",
}
# Word-boundary match so "sport" does not fire inside "transport"/"passport"
# and a domain like "transport.gov" is not mistaken for a sports site.
_SPORTS_HINT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(h) for h in _SPORTS_HINTS) + r")\b"
)
_LOW_VALUE_NEWS_DOMAINS = {
    "facebook.com", "www.facebook.com", "sports.yahoo.com", "yahoo.com",
    "www.yahoo.com", "msn.com", "www.msn.com",
}
_TRUSTED_NEWS_DOMAINS = {
    "apnews.com", "www.apnews.com", "reuters.com", "www.reuters.com",
    "bbc.com", "www.bbc.com", "cbc.ca", "www.cbc.ca",
    "ctvnews.ca", "www.ctvnews.ca", "globalnews.ca", "www.globalnews.ca",
    "theguardian.com",
    "www.theguardian.com", "euronews.com", "www.euronews.com",
    "dw.com", "www.dw.com", "government.se", "www.government.se",
}
_SECONDARY_DISCUSSION_DOMAINS = {
    "reddit.com", "www.reddit.com", "news.ycombinator.com",
    "stackoverflow.com", "www.stackoverflow.com",
    "medium.com", "www.medium.com", "dev.to", "www.dev.to",
}
_DOCS_PATH_HINTS = (
    "/docs/", "/documentation/", "/api/", "/reference/",
)
_DOCS_HOST_HINTS = (
    "docs.", "developer.", "developers.",
)
_PRODUCT_QUERY_HINTS = {
    "sdk", "api", "docs", "documentation", "library", "package",
    "typescript", "python", "endpoint", "reference",
}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _has_word(text: str, term: str) -> bool:
    """True if ``term`` appears in ``text`` as a whole word.

    Query terms are matched on word boundaries so a short term doesn't match
    inside an unrelated word: "us" must not match "business"/"music", "port"
    must not match "transport"/"support". This mirrors the tokenization used to
    build ``query_terms`` (``\\b\\w+\\b``). #1473 converted the title and sports
    checks to word boundaries; the snippet and subject-term checks below use
    the same helper so the whole file stays consistent.
    """
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def rank_search_results(query: str, results: List[dict]) -> List[dict]:
    """Rank search results by title relevance, snippet quality, domain authority, and recency."""
    query_terms = [t.lower() for t in re.findall(r"\b\w+\b", query)]
    query_lc = query.lower()
    is_news_query = any(term in _NEWS_HINTS for term in query_terms)
    is_sports_query = bool(_SPORTS_HINT_RE.search(query_lc))
    is_product_query = any(term in _PRODUCT_QUERY_HINTS for term in query_terms) or any(
        h in query_lc for h in ("/docs", "sdk", "api reference", "official docs")
    )

    def title_score(title: str) -> float:
        if not title:
            return 0.0
        title_lc = title.lower()
        matches = sum(1 for term in query_terms if _has_word(title_lc, term))
        return matches / len(query_terms) if query_terms else 0.0

    def snippet_score(snippet: str) -> float:
        if not snippet:
            return 0.0
        length_factor = min(len(snippet), 200) / 200
        term_hits = sum(1 for term in query_terms if _has_word(snippet.lower(), term))
        term_factor = term_hits / len(query_terms) if query_terms else 0.0
        return (length_factor + term_factor) / 2

    def domain_score(url: str) -> float:
        netloc = _domain(url)
        if not netloc:
            return 0.0
        if netloc in _TRUSTED_NEWS_DOMAINS:
            return 1.0
        if netloc.endswith(".edu") or netloc.endswith(".gov"):
            return 1.0
        # Official documentation hosts beat generic .org (arxiv) for product queries.
        path = ""
        try:
            path = urlparse(url).path.lower()
        except Exception:
            path = ""
        if any(h in netloc for h in _DOCS_HOST_HINTS) or any(p in path for p in _DOCS_PATH_HINTS):
            return 0.95
        if netloc.endswith(".org"):
            return 0.7
        return 0.4

    def docs_quality_adjustment(title: str, snippet: str, url: str) -> float:
        """Boost primary docs and gently demote discussion sites for product queries."""
        if not is_product_query:
            return 0.0
        u = (url or "").lower()
        netloc = _domain(url)
        path = ""
        try:
            path = urlparse(url).path.lower()
        except Exception:
            path = ""
        adjustment = 0.0
        if any(h in netloc for h in _DOCS_HOST_HINTS) or any(p in path for p in _DOCS_PATH_HINTS):
            adjustment += 1.4
        # Forum announce threads are useful corroboration, not SoT.
        if "forum." in netloc or "/forums/" in u or netloc in _SECONDARY_DISCUSSION_DOMAINS:
            adjustment -= 0.7
        text = f"{title} {snippet}".lower()
        if "official documentation" in text or "api reference" in text:
            adjustment += 0.3
        return adjustment

    def news_quality_adjustment(title: str, snippet: str, url: str) -> float:
        if not is_news_query:
            return 0.0
        text = f"{title} {snippet}".lower()
        netloc = _domain(url)
        adjustment = 0.0
        if netloc in _TRUSTED_NEWS_DOMAINS:
            adjustment += 1.2
        if any(term in text for term in ("latest news", "breaking news", "daily coverage", "news from")):
            adjustment += 0.4
        if netloc in _LOW_VALUE_NEWS_DOMAINS:
            adjustment -= 0.8
        if not is_sports_query and (_SPORTS_HINT_RE.search(text) or _SPORTS_HINT_RE.search(netloc)):
            adjustment -= 1.5
        # A country/news query should not rank a page whose title/snippet barely
        # mentions the country above actual news pages for that country.
        subject_terms = [t for t in query_terms if t not in _NEWS_HINTS]
        if subject_terms and not any(_has_word(text, t) or _has_word(netloc, t) for t in subject_terms):
            adjustment -= 1.0
        return adjustment

    ranked = []
    for result in results:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        age = result.get("age", None)

        score = (
            2.0 * title_score(title)
            + 1.0 * snippet_score(snippet)
            + 1.5 * domain_score(url)
            + 1.0 * recency_score(age)
            + news_quality_adjustment(title, snippet, url)
            + docs_quality_adjustment(title, snippet, url)
        )
        ranked.append((score, result))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in ranked]
