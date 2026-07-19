"""In-process evidence store for Deep Research span-level recall.

Uses fastembed (already a core dependency) + numpy cosine similarity.
chromadb-client in this repo is HTTP-only, so we keep an ephemeral local
index without requiring a Chroma server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvidenceHit:
    """One retrieved evidence chunk."""

    url: str
    text: str
    score: float
    title: str = ""
    chunk_idx: int = 0


class EvidenceStore:
    """Append-only evidence index with embedding-based query/coverage."""

    def __init__(self, *, embedding_model: str | None = None) -> None:
        self._embedding_model = embedding_model
        self._client = None
        self._texts: list[str] = []
        self._urls: list[str] = []
        self._titles: list[str] = []
        self._chunk_idxs: list[int] = []
        self._vectors: np.ndarray | None = None

    def _encode(self, texts: list[str]) -> np.ndarray:
        client = self._get_client()
        if client is None:
            raise RuntimeError("No embedding client available")
        vecs = client.encode(texts, normalize_embeddings=True)
        if hasattr(vecs, "astype"):
            return np.asarray(vecs, dtype="float32")
        return np.asarray(list(vecs), dtype="float32")

    def _get_client(self):
        if self._client is not None:
            return self._client
        # Prefer local fastembed for research runs (no network dependency).
        try:
            from src.embeddings import FastEmbedClient
            self._client = FastEmbedClient(model=self._embedding_model)
            return self._client
        except Exception as exc:
            logger.info("FastEmbed unavailable for EvidenceStore: %s", exc)
        try:
            from src.embeddings import get_embedding_client
            self._client = get_embedding_client()
        except Exception as exc:
            logger.warning("EvidenceStore embedding client failed: %s", exc)
            self._client = None
        return self._client

    def add_finding(
        self,
        url: str,
        chunks: list[str],
        *,
        title: str = "",
        embedding_model: str | None = None,
    ) -> int:
        """Index chunks for a source URL.

        Args:
            url: Source URL.
            chunks: Text chunks to index.
            title: Optional page title.
            embedding_model: Unused alias kept for API compatibility.

        Returns:
            Number of chunks added.
        """
        del embedding_model  # API compatibility with plan signature
        clean = [c.strip() for c in chunks if isinstance(c, str) and c.strip()]
        if not clean:
            return 0
        try:
            vecs = self._encode(clean)
        except Exception as exc:
            logger.warning("EvidenceStore encode failed: %s", exc)
            return 0

        start_idx = len(self._texts)
        for i, chunk in enumerate(clean):
            self._texts.append(chunk)
            self._urls.append(url)
            self._titles.append(title or "")
            self._chunk_idxs.append(i)

        if self._vectors is None or self._vectors.size == 0:
            self._vectors = vecs
        else:
            self._vectors = np.vstack([self._vectors, vecs])

        return len(clean) - 0 if start_idx >= 0 else len(clean)

    def query(self, question: str, k: int = 10) -> list[EvidenceHit]:
        """Return top-k evidence chunks for a question."""
        if not self._texts or self._vectors is None or self._vectors.size == 0:
            return []
        q = (question or "").strip()
        if not q:
            return []
        try:
            q_vec = self._encode([q])[0]
        except Exception as exc:
            logger.warning("EvidenceStore query encode failed: %s", exc)
            return []

        scores = self._vectors @ q_vec
        k = max(1, min(int(k or 10), len(scores)))
        top_idx = np.argpartition(-scores, kth=k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        hits: list[EvidenceHit] = []
        for idx in top_idx:
            i = int(idx)
            hits.append(
                EvidenceHit(
                    url=self._urls[i],
                    text=self._texts[i],
                    score=float(scores[i]),
                    title=self._titles[i],
                    chunk_idx=self._chunk_idxs[i],
                )
            )
        return hits

    def coverage_score(self, sub_questions: list[str]) -> float:
        """Mean of per-sub-question max cosine similarity to evidence."""
        questions = [str(q).strip() for q in sub_questions if str(q).strip()]
        if not questions or not self._texts or self._vectors is None:
            return 0.0
        try:
            q_vecs = self._encode(questions)
        except Exception as exc:
            logger.warning("EvidenceStore coverage encode failed: %s", exc)
            return 0.0

        # (Q, D) similarities
        sims = q_vecs @ self._vectors.T
        per_q = sims.max(axis=1)
        return float(np.mean(per_q))

    def rank_texts(self, query: str, texts: list[str], *, top_k: int = 3) -> list[str]:
        """Rank candidate strings by similarity to query (for outbound follow)."""
        clean = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
        if not clean:
            return []
        try:
            q_vec = self._encode([query])[0]
            t_vecs = self._encode(clean)
        except Exception:
            return clean[:top_k]
        scores = t_vecs @ q_vec
        order = np.argsort(-scores)
        return [clean[int(i)] for i in order[: max(1, top_k)]]

    def __len__(self) -> int:
        return len(self._texts)
