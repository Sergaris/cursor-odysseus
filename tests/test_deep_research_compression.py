"""Tests for Deep Research query-aware compression."""

from services.search.compression import compress_content, keyword_density


def test_heuristic_keeps_query_relevant_paragraphs():
    content = (
        "Cookie policy and subscribe to newsletter.\n\n"
        "Quantum computing uses qubits for superposition.\n\n"
        "Buy our product today with a discount code.\n\n"
        "Error correction is essential for scalable quantum computers."
    )
    out = compress_content(
        content,
        "quantum computing qubits",
        backend="heuristic",
        max_chars=2000,
    )
    assert "qubits" in out.lower()
    assert "cookie policy" not in out.lower()


def test_off_backend_passthrough():
    text = "hello world " * 100
    out = compress_content(text, "hello", backend="off", max_chars=50)
    assert len(out) <= 50


def test_provence_falls_back_to_heuristic_when_missing():
    content = "Alpha topic paragraph about widgets.\n\nUnrelated footer text."
    out = compress_content(content, "widgets", backend="provence", max_chars=500)
    assert "widgets" in out.lower()


def test_keyword_density():
    assert keyword_density("quantum computing research", ["quantum", "computing"]) == 1.0
