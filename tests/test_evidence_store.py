"""Tests for EvidenceStore with a fake embedding client (no model download)."""

import numpy as np

from src.evidence_store import EvidenceStore


class _FakeEmbed:
    def encode(self, texts, normalize_embeddings=True):
        # Deterministic bag-of-chars embedding in 8 dims.
        vecs = []
        for text in texts:
            v = np.zeros(8, dtype="float32")
            for i, ch in enumerate(text.lower()[:64]):
                v[i % 8] += (ord(ch) % 13) / 13.0
            if normalize_embeddings:
                n = np.linalg.norm(v) or 1.0
                v = v / n
            vecs.append(v)
        return np.stack(vecs)


def test_add_query_and_coverage():
    store = EvidenceStore()
    store._client = _FakeEmbed()

    n = store.add_finding(
        "https://ex.com/a",
        ["Quantum computing uses qubits.", "Baking bread needs yeast."],
        title="A",
    )
    assert n == 2
    assert len(store) == 2

    hits = store.query("quantum qubits", k=1)
    assert hits
    assert "quantum" in hits[0].text.lower()

    score = store.coverage_score(["quantum computing", "bread yeast"])
    assert 0.0 <= score <= 1.0
    assert score > 0.0


def test_rank_texts():
    store = EvidenceStore()
    store._client = _FakeEmbed()
    ranked = store.rank_texts(
        "quantum",
        ["quantum paper", "cooking recipe", "qubit news"],
        top_k=2,
    )
    assert len(ranked) == 2
    assert "quantum" in ranked[0].lower() or "qubit" in ranked[0].lower()
