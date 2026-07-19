"""Tests for text chunking used by EvidenceStore."""

from src.chunking import chunk_text, chunk_text_detailed


def test_chunk_text_splits_long_content():
    paras = [f"Paragraph {i} with enough words to matter." for i in range(40)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, max_tokens=64, overlap_tokens=8)
    assert len(chunks) >= 2
    assert all(isinstance(c, str) and c for c in chunks)


def test_chunk_text_detailed_has_offsets():
    text = "First paragraph.\n\nSecond paragraph is longer and keeps going."
    detailed = chunk_text_detailed(text, max_tokens=32, overlap_tokens=4)
    assert detailed
    assert detailed[0].chunk_idx == 0
    assert detailed[0].char_offset >= 0


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []
