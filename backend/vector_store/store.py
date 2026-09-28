"""The vault on disk: documents, their chunks, chunk vectors (FAISS), and original files.

Everything lives under one folder (default `data/index/`, git-ignored because it
holds personal documents):

    documents.json    one record per uploaded document (name, category, pages...)
    chunks.jsonl      one record per chunk; `vid` is the chunk's id in the FAISS index
    vectors.faiss     FAISS inner-product index over L2-normalized vectors (= cosine)
    meta.json         which embedding model built the vectors
    files/            the original uploads, stored as <doc_id><ext>

The index is exact (IndexFlatIP): a family has hundreds of pages, not millions, so
there is nothing to gain from approximate search, and exact results keep the
evaluation honest. Phase 2 moves the document records to PostgreSQL; Phase 13 can
swap FAISS for Qdrant behind the same methods.

Writes go to a temp file and are swapped in, so a crash mid-save can't corrupt the vault.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import faiss
import numpy as np


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _new_index(dim: int) -> faiss.Index:
    return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))


class DocumentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.documents: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.index: faiss.Index | None = None
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
        index_path = self.root / "vectors.faiss"
        self.index = faiss.read_index(str(index_path)) if index_path.exists() else None
        self.model = json.loads((self.root / "meta.json").read_text()).get("model")
        if len(self.chunks) != self.num_vectors:
            raise RuntimeError(f"{self.root} is inconsistent: {len(self.chunks)} chunks vs {self.num_vectors} vectors")

    def _save(self) -> None:
        _atomic_write(self.root / "documents.json", json.dumps(list(self.documents.values()), indent=2).encode())
        _atomic_write(
            self.root / "chunks.jsonl",
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in self.chunks).encode("utf-8"),
        )
        index_path = self.root / "vectors.faiss"
        if self.index is not None:
            tmp = self.root / "vectors.faiss.tmp"
            faiss.write_index(self.index, str(tmp))
            os.replace(tmp, index_path)
        else:
            index_path.unlink(missing_ok=True)
        _atomic_write(self.root / "meta.json", json.dumps({"model": self.model}).encode())

    # ---------- reads ----------

    @property
    def num_vectors(self) -> int:
        return self.index.ntotal if self.index is not None else 0

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

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        category: str | None = None,
        per_document: bool = True,
    ) -> list[tuple[dict, float]]:
        """[(chunk, score), ...] best first.

        per_document=True keeps only each document's best chunk (a results list);
        False returns the top chunks regardless of document (context for the LLM).
        """
        with self._lock:
            if not self.num_vectors:
                return []
            # Rank everything: exact search over a family-sized vault takes microseconds,
            # and filtering/deduplicating after a short top-k could leave too few results.
            query = np.ascontiguousarray(query_embedding.reshape(1, -1), dtype=np.float32)
            scores, vids = self.index.search(query, self.num_vectors)
            by_vid = {c["vid"]: c for c in self.chunks}
            results, seen = [], set()
            for score, vid in zip(scores[0], vids[0]):
                chunk = by_vid.get(int(vid))
                if chunk is None or (category and chunk["category"] != category):
                    continue
                if per_document:
                    if chunk["doc_id"] in seen:
                        continue
                    seen.add(chunk["doc_id"])
                results.append((chunk, float(score)))
                if len(results) == top_k:
                    break
            return results

    # ---------- writes ----------

    def add(self, document: dict, chunks: list[dict], embeddings: np.ndarray, model: str) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
        with self._lock:
            if self.model and self.model != model:
                raise ValueError(f"vault was built with {self.model}, not {model}; re-embed it first")
            self.model = model
            next_vid = max((c["vid"] for c in self.chunks), default=-1) + 1
            chunks = [{**c, "vid": next_vid + i} for i, c in enumerate(chunks)]
            if len(chunks):
                vectors = np.ascontiguousarray(embeddings, dtype=np.float32)
                if self.index is None:
                    self.index = _new_index(vectors.shape[1])
                self.index.add_with_ids(vectors, np.array([c["vid"] for c in chunks], dtype=np.int64))
            self.documents[document["doc_id"]] = document
            self.chunks.extend(chunks)
            self._save()

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.documents.pop(doc_id, None)
            if doc is None:
                return False
            removed = [c["vid"] for c in self.chunks if c["doc_id"] == doc_id]
            self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id]
            if removed and self.index is not None:
                self.index.remove_ids(np.array(removed, dtype=np.int64))
            (self.files_dir / doc["stored_as"]).unlink(missing_ok=True)
            self._save()
            return True

    def reembed(self, embed_passages, model: str) -> None:
        """Rebuild all vectors with a different model (chunk texts are kept, so no re-parsing)."""
        with self._lock:
            self.index = None
            if self.chunks:
                vectors = np.ascontiguousarray(embed_passages(self.chunks), dtype=np.float32)
                self.index = _new_index(vectors.shape[1])
                self.index.add_with_ids(vectors, np.array([c["vid"] for c in self.chunks], dtype=np.int64))
            self.model = model
            self._save()
