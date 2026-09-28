"""Answer one query at a time: the retrieval path the API serves.

Holds the chunks and their embeddings in memory and returns the top-k
*passages* (best chunk per document) for a query. The eval script scores
thousands of queries in bulk; this is the same vector search, one query at a time,
with the chunk text attached so a person can read the results.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from src.retrieval.chunk_store import EmbedderLike
from src.retrieval.vector_index import NumpyIndex


@dataclass
class Hit:
    rank: int
    score: float
    doc_id: str
    chunk_id: str
    title: str
    text: str


class VectorSearcher:
    def __init__(self, chunks: list[dict], embeddings: np.ndarray, embedder: EmbedderLike):
        if len(chunks) != len(embeddings):
            raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
        self.chunks = chunks
        self.index = NumpyIndex(embeddings)
        self.embedder = embedder

    def search(self, query: str, top_k: int = 10) -> tuple[list[Hit], float]:
        """Return (hits, elapsed_ms). One hit per document, best first."""
        t0 = time.perf_counter()
        query_emb = self.embedder.encode_queries([query])
        # Over-fetch chunks so that merging chunks of one document still leaves top_k documents.
        scores, idx = self.index.search(query_emb, top_k=top_k * 2)
        hits: list[Hit] = []
        seen: set[str] = set()
        for score, i in zip(scores[0], idx[0]):
            chunk = self.chunks[i]
            if chunk["doc_id"] in seen:
                continue
            seen.add(chunk["doc_id"])
            hits.append(
                Hit(len(hits) + 1, float(score), chunk["doc_id"], chunk["chunk_id"], chunk["title"], chunk["text"])
            )
            if len(hits) == top_k:
                break
        return hits, (time.perf_counter() - t0) * 1000
