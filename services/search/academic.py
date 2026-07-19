"""Academic / specialized search adapters for Deep Research.

Provides arXiv, Semantic Scholar, and GitHub public search without auth.
Returns the same shape as web providers: ``[{url, title, snippet, age?}]``.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_ARXIV_API = "https://export.arxiv.org/api/query"
_S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_GITHUB_API = "https://api.github.com/search/issues"
_USER_AGENT = "OdysseusDeepResearch/1.0 (+https://github.com/pewdiepie-archdaemon/odysseus)"


def search_academic(query: str, strategy: str = "academic", count: int = 10) -> list[dict]:
    """Route a strategy-tagged query to the matching specialized adapter."""
    strategy = (strategy or "academic").strip().lower()
    count = max(1, min(int(count or 10), 25))
    q = (query or "").strip()
    if not q:
        return []

    if strategy == "github":
        return github_search(q, count)
    if strategy == "pdf":
        # Prefer arXiv for PDF-oriented academic queries; fall back to S2.
        hits = arxiv_search(q, count)
        if hits:
            return hits
        return semantic_scholar_search(q, count)
    # academic / default
    hits = arxiv_search(q, count)
    if len(hits) >= max(3, count // 2):
        return hits
    s2 = semantic_scholar_search(q, count)
    # Merge, prefer arXiv first, then S2 uniques.
    seen = {h.get("url") for h in hits}
    for item in s2:
        if item.get("url") not in seen:
            hits.append(item)
            seen.add(item.get("url"))
        if len(hits) >= count:
            break
    return hits[:count]


def arxiv_search(query: str, count: int = 10) -> list[dict]:
    """Search arXiv Atom API."""
    cleaned = _strip_operators(query)
    params = {
        "search_query": f"all:{cleaned}",
        "start": 0,
        "max_results": count,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = f"{_ARXIV_API}?{urlencode(params)}"
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return _parse_arxiv_atom(resp.text, limit=count)
    except Exception as exc:
        logger.warning("arXiv search failed: %s", exc)
        return []


def semantic_scholar_search(query: str, count: int = 10) -> list[dict]:
    """Search Semantic Scholar Graph API (public, no key)."""
    cleaned = _strip_operators(query)
    params = {
        "query": cleaned,
        "limit": count,
        "fields": "title,abstract,url,year,externalIds",
    }
    try:
        with httpx.Client(timeout=20.0, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(_S2_API, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Semantic Scholar search failed: %s", exc)
        return []

    out: list[dict] = []
    for paper in data.get("data") or []:
        if not isinstance(paper, dict):
            continue
        title = (paper.get("title") or "").strip() or "Untitled paper"
        abstract = (paper.get("abstract") or "").strip()
        paper_url = (paper.get("url") or "").strip()
        ext = paper.get("externalIds") or {}
        if not paper_url and isinstance(ext, dict) and ext.get("ArXiv"):
            paper_url = f"https://arxiv.org/abs/{ext['ArXiv']}"
        if not paper_url:
            continue
        age = str(paper.get("year") or "")
        out.append({
            "url": paper_url,
            "title": title,
            "snippet": abstract[:500],
            "age": age,
            "provider": "semantic_scholar",
        })
        if len(out) >= count:
            break
    return out


def github_search(query: str, count: int = 10) -> list[dict]:
    """Search public GitHub issues/PRs (unauthenticated rate-limited)."""
    cleaned = _strip_operators(query)
    # Prefer issues; allow plain text query.
    q = cleaned
    if "is:" not in q.lower():
        q = f"{cleaned} is:issue"
    params = {
        "q": q,
        "per_page": count,
        "sort": "relevance",
        "order": "desc",
    }
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    try:
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(_GITHUB_API, params=params)
            if resp.status_code == 403:
                logger.warning("GitHub search rate-limited")
                return []
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("GitHub search failed: %s", exc)
        return []

    out: list[dict] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        html_url = (item.get("html_url") or "").strip()
        title = (item.get("title") or "").strip() or "GitHub issue"
        body = (item.get("body") or "").strip()
        if not html_url:
            continue
        out.append({
            "url": html_url,
            "title": title,
            "snippet": body[:500],
            "age": (item.get("updated_at") or "")[:10],
            "provider": "github",
        })
        if len(out) >= count:
            break
    return out


def _strip_operators(query: str) -> str:
    """Remove site:/filetype: operators that confuse specialized APIs."""
    tokens = []
    for token in (query or "").split():
        low = token.lower()
        if low.startswith("site:") or low.startswith("filetype:") or low.startswith("after:"):
            continue
        tokens.append(token)
    return " ".join(tokens).strip() or (query or "").strip()


def _parse_arxiv_atom(xml_text: str, *, limit: int) -> list[dict]:
    """Parse arXiv Atom XML into provider-shaped dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("arXiv XML parse failed: %s", exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        title = " ".join(title.split())
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        summary = " ".join(summary.split())
        published = (entry.findtext("atom:published", default="", namespaces=ns) or "")[:10]
        link = ""
        for link_el in entry.findall("atom:link", ns):
            href = link_el.attrib.get("href", "")
            rel = link_el.attrib.get("rel", "")
            title_attr = link_el.attrib.get("title", "")
            if rel == "alternate" or not link:
                link = href
            if title_attr == "pdf":
                # Prefer abstract page over direct pdf for fetchability.
                pass
        if not link:
            id_text = entry.findtext("atom:id", default="", namespaces=ns) or ""
            link = id_text.strip()
        if not link:
            continue
        out.append({
            "url": link,
            "title": title or "arXiv paper",
            "snippet": summary[:500],
            "age": published,
            "provider": "arxiv",
        })
        if len(out) >= limit:
            break
    return out
