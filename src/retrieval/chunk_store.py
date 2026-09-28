"""Load step 1 chunks and their embeddings, embedding them only when needed.

Embedding ~100k chunks takes minutes on a laptop, so the matrix is cached next
to the chunks and reused as long as the chunks file, model and passage format
are unchanged. Shared by the eval script and the search API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from src.retrieval.embedder import format_passage

logger = logging.getLogger(__name__)


class EmbedderLike(Protocol):
    model_name: str

    def encode_passages(self, texts: list[str]) -> np.ndarray: ...

    def encode_queries(self, texts: list[str]) -> np.ndarray: ...


def load_chunks(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_or_embed_chunks(
    chunks: list[dict], chunks_path: Path, embedder: EmbedderLike, cache_dir: Path, with_title: bool
) -> np.ndarray:
    """Embed all chunks, reusing a cached matrix if it was built from the same
    chunks file, model, and passage format."""
    key = {"model": embedder.model_name, "chunks_sha1": file_sha1(chunks_path), "with_title": with_title}
    stem = cache_dir / embedder.model_name.replace("/", "__")
    npy_path, meta_path = stem.with_suffix(".npy"), stem.with_suffix(".meta.json")
    if npy_path.exists() and meta_path.exists() and json.loads(meta_path.read_text()) == key:
        logger.info("Using cached chunk embeddings %s", npy_path)
        return np.load(npy_path)

    logger.info("Embedding %d chunks with %s (cached after this run)", len(chunks), embedder.model_name)
    t0 = time.time()
    texts = [format_passage(c["title"], c["text"], with_title) for c in chunks]
    emb = embedder.encode_passages(texts)
    logger.info("Embedded in %.0fs", time.time() - t0)
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(npy_path, emb)
    meta_path.write_text(json.dumps(key))
    return emb
