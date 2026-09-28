"""The family's document library: files, their chunks, and chunk embeddings.

Everything lives under one folder (default `data/index/`, git-ignored because it
holds personal documents):

    documents.json    one record per uploaded document (name, category, pages...)
    chunks.jsonl      one record per chunk, row i matches embeddings row i
    embeddings.npy    (n_chunks, dim) float32, L2-normalized
    meta.json         which embedding model built the vectors
    files/            the original uploads, stored as <doc_id><ext>

A family's documents are hundreds of pages, not millions, so search is exact
cosine similarity over the whole matrix in NumPy. Writes go to a temp file and
are swapped in, so a crash mid-save can't corrupt the library.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class DocumentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.documents: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.embeddings = np.zeros((0, 0), dtype=np.float32)
        self.model: str | None = None
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        docs_path = self.root / "documents.json"
        if not docs_path.exists():
            return
        self.documents = {d["doc_id"]: d for d in json.loads(docs_path.read_text())}
        with open(self.root / "chunks.jsonl", encoding="utf-8") as f:
            self.chunks = [json.loads(line) for line in f]
        self.embeddings = np.load(self.root / "embeddings.npy")
        self.model = json.loads((self.root / "meta.json").read_text()).get("model")
        if len(self.chunks) != len(self.embeddings):
            raise RuntimeError(f"{self.root} is inconsistent: {len(self.chunks)} chunks vs {len(self.embeddings)} vectors")

    def _save(self) -> None:
        _atomic_write(self.root / "documents.json", json.dumps(list(self.documents.values()), indent=2).encode())
        _atomic_write(
            self.root / "chunks.jsonl",
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in self.chunks).encode("utf-8"),
        )
        tmp = self.root / "embeddings.tmp.npy"
        np.save(tmp, self.embeddings)
        os.replace(tmp, self.root / "embeddings.npy")
        _atomic_write(self.root / "meta.json", json.dumps({"model": self.model}).encode())

    # ---------- reads ----------

    def list_documents(self) -> list[dict]:
        with self._lock:
            return sorted(self.documents.values(), key=lambda d: d["added_at"], reverse=True)

    def get(self, doc_id: str) -> dict | None:
        return self.documents.get(doc_id)

    def find_by_sha256(self, sha256: str) -> dict | None:
        return next((d for d in self.documents.values() if d["sha256"] == sha256), None)

    def file_path(self, doc_id: str) -> Path | None:
        doc = self.documents.get(doc_id)
        return self.files_dir / doc["stored_as"] if doc else None

    def search(self, query_embedding: np.ndarray, top_k: int = 10, category: str | None = None) -> list[tuple[dict, float]]:
        """Best-matching chunk per document, best document first: [(chunk, score), ...]."""
        with self._lock:
            if not self.chunks:
                return []
            scores = self.embeddings @ query_embedding.reshape(-1)
            if category:
                allowed = np.array([self.documents[c["doc_id"]]["category"] == category for c in self.chunks])
                scores = np.where(allowed, scores, -np.inf)
            results, seen = [], set()
            for i in np.argsort(-scores):
                if not np.isfinite(scores[i]):
                    break
                chunk = self.chunks[i]
                if chunk["doc_id"] in seen:
                    continue
                seen.add(chunk["doc_id"])
                results.append((chunk, float(scores[i])))
                if len(results) == top_k:
                    break
            return results

    # ---------- writes ----------

    def add(self, document: dict, chunks: list[dict], embeddings: np.ndarray, model: str) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
        with self._lock:
            if self.model and self.model != model:
                raise ValueError(f"library was built with {self.model}, not {model}; re-embed it first")
            self.model = model
            self.documents[document["doc_id"]] = document
            self.chunks.extend(chunks)
            emb = embeddings.astype(np.float32)
            self.embeddings = emb if self.embeddings.size == 0 else np.vstack([self.embeddings, emb])
            self._save()

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.documents.pop(doc_id, None)
            if doc is None:
                return False
            keep = [i for i, c in enumerate(self.chunks) if c["doc_id"] != doc_id]
            self.chunks = [self.chunks[i] for i in keep]
            self.embeddings = self.embeddings[keep] if keep else np.zeros((0, 0), dtype=np.float32)
            (self.files_dir / doc["stored_as"]).unlink(missing_ok=True)
            self._save()
            return True

    def reembed(self, embed_passages, model: str) -> None:
        """Rebuild all vectors with a different model (chunk texts are kept, so no re-parsing)."""
        with self._lock:
            if self.chunks:
                self.embeddings = embed_passages(self.chunks).astype(np.float32)
            self.model = model
            self._save()
