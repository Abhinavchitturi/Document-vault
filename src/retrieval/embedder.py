"""Turn chunks and queries into normalized embedding vectors.

bge and e5 are asymmetric retrievers: queries and passages are encoded with
different prefixes, and leaving the query prefix off measurably hurts recall.
Vectors are L2-normalized so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Prefixes recommended by each model's authors for query -> passage retrieval.
_PREFIXES = {
    "bge": ("Represent this sentence for searching relevant passages: ", ""),
    "e5": ("query: ", "passage: "),
}


def model_prefixes(model_name: str) -> tuple[str, str]:
    """Return (query_prefix, passage_prefix) for a model name."""
    name = model_name.lower()
    for family, prefixes in _PREFIXES.items():
        if family in name:
            return prefixes
    return "", ""


def format_passage(title: str, text: str, with_title: bool = True) -> str:
    """Text we embed for a chunk. Titles carry the entity name, which many NQ
    passages never repeat in their body (e.g. a "Lee Baldwin" passage that only
    says "Lee")."""
    return f"{title} {text}" if with_title and title else text


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None, batch_size: int = 64):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, device=device)  # device=None picks cuda/mps/cpu
        self.query_prefix, self.passage_prefix = model_prefixes(model_name)

    def _encode(self, texts: list[str], prefix: str) -> np.ndarray:
        vecs = self.model.encode(
            [prefix + t for t in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > self.batch_size,
        )
        return vecs.astype(np.float32)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, self.passage_prefix)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, self.query_prefix)
