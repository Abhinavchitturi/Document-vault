"""The vault: document metadata in PostgreSQL, vectors in FAISS, original files on disk.

    PostgreSQL (backend/database/)   users, documents, document_chunks, vault_meta
    <root>/vectors.faiss             FAISS inner-product index; vector id = document_chunks.id
    <root>/files/                    the original uploads, as <doc_id><ext>

`root` defaults to data/index/ (git-ignored: it holds personal documents).

Each store works for one owner (Phase 2: every document has an owner; Phase 6
adds login so each family member gets their own). Searches and listings only
see that owner's documents.

The FAISS index is exact (IndexFlatIP): a family has hundreds of pages, not
millions. Phase 13 can swap it for Qdrant behind the same methods.

Consistency: document rows and their chunks are written in one database
transaction, and the FAISS file is saved before that transaction commits. If
the two ever disagree (a crash between them), `missing_vectors` says so and the
app rebuilds the vectors from the stored chunk text on startup.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import delete, func, insert, select

from backend.database.db import connect
from backend.database.schema import document_chunks, documents, users, vault_meta


def _new_index(dim: int) -> faiss.Index:
    return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite drops the zone


def _default_url(root: Path) -> str:
    """Used when no database URL is given (tests, quick experiments): a SQLite file in the vault folder."""
    return f"sqlite:///{root / 'vault.db'}"


class DocumentStore:
    def __init__(self, root: str | Path, database_url: str | None = None, owner: str = "family"):
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.engine = connect(database_url or _default_url(self.root))
        self._lock = threading.RLock()
        self.owner = owner
        self.owner_id = self._ensure_user(owner)
        self.documents: dict[str, dict] = {}  # this owner's documents, cached
        self.chunks: list[dict] = []  # this owner's chunks, cached for search
        self.index: faiss.Index | None = None
        self.model: str | None = None
        self._migrate_json_vault()
        self._load()

    # ---------- database ----------

    def _ensure_user(self, name: str) -> int:
        with self.engine.begin() as conn:
            user_id = conn.execute(select(users.c.id).where(users.c.name == name)).scalar()
            if user_id is None:
                user_id = conn.execute(
                    insert(users).values(name=name, created_at=datetime.now(timezone.utc)).returning(users.c.id)
                ).scalar_one()
            return user_id

    def _meta(self, conn, key: str) -> str | None:
        return conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == key)).scalar()

    def _set_meta(self, conn, key: str, value: str) -> None:
        conn.execute(delete(vault_meta).where(vault_meta.c.key == key))
        conn.execute(insert(vault_meta).values(key=key, value=value))

    @staticmethod
    def _doc_row(document: dict, owner_id: int) -> dict:
        return {
            "id": document["doc_id"], "owner_id": owner_id, "name": document["filename"],
            "doc_type": document["category"], "uploaded_at": datetime.fromisoformat(document["added_at"]),
            "file_path": document["stored_as"], "pages": document["pages"], "ocr_pages": document.get("ocr_pages", 0),
            "num_chunks": document["num_chunks"], "size_bytes": document["size_bytes"], "sha256": document["sha256"],
        }

    @staticmethod
    def _chunk_row(chunk: dict, index_in_doc: int) -> dict:
        return {"id": chunk["vid"], "document_id": chunk["doc_id"], "page": chunk["page"],
                "chunk_index": index_in_doc, "text": chunk["text"], "n_tokens": chunk["n_tokens"]}

    def _load(self) -> None:
        with self.engine.connect() as conn:
            self.model = self._meta(conn, "embedding_model")
            rows = conn.execute(select(documents).where(documents.c.owner_id == self.owner_id)).mappings().all()
            self.documents = {
                r["id"]: {
                    "doc_id": r["id"], "filename": r["name"], "category": r["doc_type"], "owner": self.owner,
                    "pages": r["pages"], "ocr_pages": r["ocr_pages"], "num_chunks": r["num_chunks"],
                    "size_bytes": r["size_bytes"], "sha256": r["sha256"], "stored_as": r["file_path"],
                    "added_at": _as_utc(r["uploaded_at"]).isoformat(timespec="seconds"),
                }
                for r in rows
            }
            chunk_rows = conn.execute(
                select(document_chunks)
                .join(documents, documents.c.id == document_chunks.c.document_id)
                .where(documents.c.owner_id == self.owner_id)
                .order_by(document_chunks.c.id)
            ).mappings().all()
        self.chunks = [
            {
                "vid": r["id"], "chunk_id": f"{r['document_id']}-p{r['page']}-{r['chunk_index']}",
                "doc_id": r["document_id"], "filename": self.documents[r["document_id"]]["filename"],
                "category": self.documents[r["document_id"]]["category"], "page": r["page"],
                "text": r["text"], "n_tokens": r["n_tokens"],
            }
            for r in chunk_rows
        ]
        index_path = self.root / "vectors.faiss"
        self.index = faiss.read_index(str(index_path)) if index_path.exists() else None

    def _save_index(self) -> None:
        index_path = self.root / "vectors.faiss"
        if self.index is not None and self.index.ntotal:
            tmp = self.root / "vectors.faiss.tmp"
            faiss.write_index(self.index, str(tmp))
            os.replace(tmp, index_path)
        else:
            index_path.unlink(missing_ok=True)

    def _migrate_json_vault(self) -> None:
        """Bring a vault made before Phase 2 (documents.json + chunks.jsonl) into the database, once."""
        docs_path, chunks_path = self.root / "documents.json", self.root / "chunks.jsonl"
        if not docs_path.exists():
            return
        docs = json.loads(docs_path.read_text())
        with open(chunks_path, encoding="utf-8") as f:
            chunks = [json.loads(line) for line in f]
        per_doc: dict[str, int] = {}
        with self.engine.begin() as conn:
            for doc in docs:
                conn.execute(insert(documents).values(**self._doc_row(doc, self.owner_id)))
            for c in chunks:
                i = per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], -1) + 1
                conn.execute(insert(document_chunks).values(**self._chunk_row(c, i)))
            meta_path = self.root / "meta.json"
            if meta_path.exists() and json.loads(meta_path.read_text()).get("model"):
                self._set_meta(conn, "embedding_model", json.loads(meta_path.read_text())["model"])
        for path in (docs_path, chunks_path, self.root / "meta.json"):
            if path.exists():
                path.rename(path.with_name(path.name + ".migrated"))

    # ---------- reads ----------

    @property
    def num_vectors(self) -> int:
        return self.index.ntotal if self.index is not None else 0

    @property
    def missing_vectors(self) -> bool:
        """True if some stored chunk has no vector (e.g. a crash mid-save); rebuild with reembed()."""
        if not self.chunks:
            return False
        if self.index is None:
            return True
        vids = np.array([c["vid"] for c in self.chunks], dtype=np.int64)
        try:
            self.index.reconstruct_batch(vids)
        except RuntimeError:
            return True
        return False

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
            if not self.num_vectors or not self.chunks:
                return []
            # Rank everything: exact search over a family-sized vault takes microseconds,
            # and filtering by owner/category after a short top-k could leave too few results.
            query = np.ascontiguousarray(query_embedding.reshape(1, -1), dtype=np.float32)
            scores, vids = self.index.search(query, self.num_vectors)
            by_vid = {c["vid"]: c for c in self.chunks}  # only this owner's chunks
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

    def counts_by_category(self) -> dict[str, int]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(documents.c.doc_type, func.count()).where(documents.c.owner_id == self.owner_id)
                .group_by(documents.c.doc_type)
            ).all()
        return {doc_type: n for doc_type, n in rows}

    # ---------- writes ----------

    def _next_vid(self, conn) -> int:
        highest = conn.execute(select(func.max(document_chunks.c.id))).scalar()
        return 0 if highest is None else highest + 1

    def add(self, document: dict, chunks: list[dict], embeddings: np.ndarray, model: str) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
        with self._lock, self.engine.begin() as conn:
            stored_model = self._meta(conn, "embedding_model")
            if stored_model and stored_model != model:
                raise ValueError(f"vault was built with {stored_model}, not {model}; re-embed it first")
            first_vid = self._next_vid(conn)
            chunks = [{**c, "vid": first_vid + i} for i, c in enumerate(chunks)]
            conn.execute(insert(documents).values(**self._doc_row(document, self.owner_id)))
            if chunks:
                conn.execute(insert(document_chunks), [self._chunk_row(c, i) for i, c in enumerate(chunks)])
            if not stored_model:
                self._set_meta(conn, "embedding_model", model)
            if chunks:
                vectors = np.ascontiguousarray(embeddings, dtype=np.float32)
                if self.index is None:
                    self.index = _new_index(vectors.shape[1])
                self.index.add_with_ids(vectors, np.array([c["vid"] for c in chunks], dtype=np.int64))
                self._save_index()  # before commit: if this fails, the rows roll back
            self.model = model
            self.documents[document["doc_id"]] = {**document, "owner": self.owner}
            self.chunks.extend(chunks)

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.documents.get(doc_id)
            if doc is None:
                return False
            removed = [c["vid"] for c in self.chunks if c["doc_id"] == doc_id]
            with self.engine.begin() as conn:
                conn.execute(delete(document_chunks).where(document_chunks.c.document_id == doc_id))
                conn.execute(delete(documents).where(documents.c.id == doc_id))
            # Vectors go after the rows: a leftover vector with no row is ignored by search.
            if removed and self.index is not None:
                self.index.remove_ids(np.array(removed, dtype=np.int64))
                self._save_index()
            del self.documents[doc_id]
            self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id]
            (self.files_dir / doc["stored_as"]).unlink(missing_ok=True)
            return True

    def reembed(self, embed_passages, model: str) -> None:
        """Rebuild all vectors (new model, or repair) from the stored chunk text; no re-parsing."""
        with self._lock:
            self.index = None
            if self.chunks:
                vectors = np.ascontiguousarray(embed_passages(self.chunks), dtype=np.float32)
                self.index = _new_index(vectors.shape[1])
                self.index.add_with_ids(vectors, np.array([c["vid"] for c in self.chunks], dtype=np.int64))
            self._save_index()
            with self.engine.begin() as conn:
                self._set_meta(conn, "embedding_model", model)
            self.model = model
