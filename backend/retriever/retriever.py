"""Turn a question into the passages that answer it.

Phase 5: hybrid search. Two rankings of every passage are combined:
    vector   meaning (the embedding model): "when does my bike cover end" -> "expires on"
    keyword  exact words and numbers (BM25, bm25.py): "KMI/PC/25/0093318", "AP7-2291-0457"
with Reciprocal Rank Fusion: each passage scores sum(1 / (60 + rank)) over the rankings it
appears in. It needs no score calibration between the two methods, and a passage near the
top of either list rises.

One rule on top: if the question contains a number copied from a document (a policy, serial,
account or phone number), passages containing that exact number come first. Otherwise a
vector model that confidently prefers a similar-looking document could still win the fusion.

`method` can also be "vector" or "keyword" alone (the evaluation compares all three).
"""

from __future__ import annotations

from backend.retriever.bm25 import KeywordSearch
from backend.vector_store.store import DocumentStore, top_results

METHODS = ("hybrid", "vector", "keyword")
RRF_K = 60


def fuse(rankings: list[list[tuple[dict, float]]], k: int = RRF_K) -> list[tuple[dict, float]]:
    """Reciprocal Rank Fusion of several [(chunk, score)] rankings, best first."""
    scores: dict[str, float] = {}
    chunks: dict[str, dict] = {}
    for ranking in rankings:
        for rank, (chunk, _) in enumerate(ranking, start=1):
            key = chunk["chunk_id"]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            chunks[key] = chunk
    order = sorted(scores, key=lambda key: -scores[key])
    return [(chunks[key], scores[key]) for key in order]


class Retriever:
    def __init__(self, store: DocumentStore, embedder, method: str = "hybrid"):
        if method not in METHODS:
            raise ValueError(f"unknown search method {method!r}; use one of {METHODS}")
        self.store = store
        self.embedder = embedder
        self.method = method
        self.keywords = KeywordSearch(store)

    def rank(self, question: str, category: str | None = None) -> list[tuple[dict, float]]:
        """Every matching passage, best first."""
        if self.method == "keyword":
            return self.keywords.rank(question, category)
        vector = self.store.rank(self.embedder.encode_queries([question])[0], category)
        if self.method == "vector":
            return vector
        fused = fuse([vector, self.keywords.rank(question, category)])
        exact = self.keywords.exact_matches(question)
        if exact:  # sorted() is stable: fused order is kept within each group
            fused = sorted(fused, key=lambda r: -exact.get(r[0]["chunk_id"], 0))
        return fused

    def documents(self, question: str, top_k: int = 5, category: str | None = None) -> list[tuple[dict, float]]:
        """Best passage per document, for a results list ("find the washing machine warranty")."""
        return top_results(self.rank(question, category), top_k, per_document=True)

    def context(self, question: str, top_k: int = 5, category: str | None = None) -> list[tuple[dict, float]]:
        """Top passages regardless of document, for the LLM: the answer may need two pages of one policy."""
        return top_results(self.rank(question, category), top_k, per_document=False)
