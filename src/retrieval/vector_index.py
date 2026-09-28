"""Exact cosine-similarity search over chunk embeddings.

For the eval baseline we use brute-force search in NumPy rather than an
approximate index (Qdrant/HNSW): 100k x 384 floats is ~150 MB, a full pass over
all test queries takes seconds, and exact search means the baseline number
measures the embedding model alone, not index approximation error. Qdrant comes
in when we build the served API.
"""

from __future__ import annotations

import numpy as np


class NumpyIndex:
    def __init__(self, embeddings: np.ndarray):
        """`embeddings`: (n_chunks, dim), L2-normalized, row i = chunk i."""
        self.embeddings = np.ascontiguousarray(embeddings, dtype=np.float32)

    def search(self, queries: np.ndarray, top_k: int, batch_size: int = 256) -> tuple[np.ndarray, np.ndarray]:
        """Return (scores, chunk_indices), each (n_queries, top_k), best first."""
        top_k = min(top_k, len(self.embeddings))
        all_scores, all_idx = [], []
        for start in range(0, len(queries), batch_size):
            sims = queries[start : start + batch_size] @ self.embeddings.T
            # argpartition finds the top_k unordered in O(n); then sort just those.
            idx = np.argpartition(-sims, top_k - 1, axis=1)[:, :top_k]
            part = np.take_along_axis(sims, idx, axis=1)
            order = np.argsort(-part, axis=1)
            all_idx.append(np.take_along_axis(idx, order, axis=1))
            all_scores.append(np.take_along_axis(part, order, axis=1))
        return np.vstack(all_scores), np.vstack(all_idx)
