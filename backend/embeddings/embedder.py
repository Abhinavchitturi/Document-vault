"""Turn chunks and queries into normalized embedding vectors.

bge and e5 are asymmetric retrievers: queries and passages are encoded with
different prefixes, and leaving the query prefix off measurably hurts recall.
Vectors are L2-normalized so cosine similarity is a plain dot product.

The model runs on this computer. It is downloaded from Hugging Face once; after
that `prefer_offline` switches the Hugging Face libraries to offline mode so
starting the vault makes no network requests at all.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    """Text we embed for a chunk: its title (document name and category), then the
    text. Page two of a policy rarely repeats what the policy is for."""
    return f"{title} {text}" if with_title and title else text


def _hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def prefer_offline(model_name: str) -> bool:
    """If `model_name` is already downloaded, forbid Hugging Face network access.

    Must run before transformers / sentence-transformers are imported, since they
    read these settings at import time. Returns True if offline mode was enabled.
    """
    snapshots = _hf_cache_dir() / f"models--{model_name.replace('/', '--')}" / "snapshots"
    if snapshots.is_dir() and any(snapshots.iterdir()):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None, batch_size: int = 64):
        prefer_offline(model_name)
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
