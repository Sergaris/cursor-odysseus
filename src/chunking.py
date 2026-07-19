"""Paragraph-aware text chunking for Deep Research evidence indexing."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One chunk of source text with grounding offsets."""

    text: str
    chunk_idx: int
    char_offset: int


_WHITESPACE_RE = re.compile(r"\s+")


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) without a tokenizer dependency."""
    return max(1, (len(text) + 3) // 4)


def chunk_text(
    text: str,
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[str]:
    """Split text into overlapping chunks near paragraph boundaries.

    Args:
        text: Source page / evidence text.
        max_tokens: Soft max tokens per chunk.
        overlap_tokens: Approximate overlap between consecutive chunks.

    Returns:
        List of chunk strings (empty if input is blank).
    """
    chunks = chunk_text_detailed(
        text,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
    )
    return [c.text for c in chunks]


def chunk_text_detailed(
    text: str,
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 64,
) -> list[TextChunk]:
    """Split text and return chunks with indices and char offsets."""
    raw = (text or "").strip()
    if not raw:
        return []

    max_tokens = max(64, int(max_tokens or 512))
    overlap_tokens = max(0, min(int(overlap_tokens or 0), max_tokens // 2))
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [_WHITESPACE_RE.sub(" ", raw)]

    # Rebuild with original offsets for grounding.
    detailed: list[TextChunk] = []
    buf = ""
    buf_start = 0
    cursor = 0
    chunk_idx = 0

    def _flush(force: bool = False) -> None:
        nonlocal buf, buf_start, chunk_idx
        if not buf:
            return
        if not force and _approx_tokens(buf) < max_tokens:
            return
        detailed.append(TextChunk(text=buf.strip(), chunk_idx=chunk_idx, char_offset=buf_start))
        chunk_idx += 1
        if overlap_chars > 0 and len(buf) > overlap_chars:
            overlap = buf[-overlap_chars:]
            buf_start = buf_start + max(0, len(buf) - len(overlap))
            buf = overlap
        else:
            buf = ""
            buf_start = cursor

    for para in paragraphs:
        # Find paragraph start in raw for offset tracking (best-effort).
        found = raw.find(para, cursor)
        if found >= 0:
            cursor = found
        if not buf:
            buf_start = cursor
        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if _approx_tokens(candidate) <= max_tokens:
            buf = candidate
            cursor = found + len(para) if found >= 0 else cursor + len(para)
            continue

        # Current buffer is full — flush, then place paragraph (possibly split).
        _flush(force=True)
        if _approx_tokens(para) <= max_tokens:
            buf = para
            buf_start = found if found >= 0 else cursor
            cursor = (found + len(para)) if found >= 0 else cursor + len(para)
            continue

        # Hard-split oversized paragraph by sentences / windows.
        start = 0
        while start < len(para):
            end = min(len(para), start + max_chars)
            piece = para[start:end].strip()
            if piece:
                detailed.append(
                    TextChunk(
                        text=piece,
                        chunk_idx=chunk_idx,
                        char_offset=(found + start) if found >= 0 else cursor + start,
                    )
                )
                chunk_idx += 1
            if end >= len(para):
                break
            start = max(end - overlap_chars, start + 1)
        buf = ""
        cursor = (found + len(para)) if found >= 0 else cursor + len(para)

    _flush(force=True)
    return detailed
