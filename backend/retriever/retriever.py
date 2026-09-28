"""Turn a question into the passages that answer it.

Phase 1 is vector search only. Phase 5 adds BM25 and fuses both rankings here,
behind the same two methods, so the API and the LLM step don't change.
"""

from __future__ import annotations

from backend.vector_store.store import DocumentStore


class Retriever:
    def __init__(self, store: DocumentStore, embedder):
        self.store = store
        self.embedder = embedder

    def _embed(self, question: str):
        return self.embedder.encode_queries([question])[0]

    def documents(self, question: str, top_k: int = 5, category: str | None = None) -> list[tuple[dict, float]]:
        """Best passage per document, for a results list ("find the washing machine warranty")."""
        return self.store.search(self._embed(question), top_k=top_k, category=category, per_document=True)

    def context(self, question: str, top_k: int = 5, category: str | None = None) -> list[tuple[dict, float]]:
        """Top passages regardless of document, for the LLM: the answer may need two pages of one policy."""
        return self.store.search(self._embed(question), top_k=top_k, category=category, per_document=False)
