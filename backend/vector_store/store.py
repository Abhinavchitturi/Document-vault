"""The vault: document metadata in PostgreSQL, vectors in FAISS, original files on disk.

    PostgreSQL (backend/database/)   users, documents, document_chunks, vault_meta
    <root>/vectors.faiss             FAISS inner-product index; vector id = document_chunks.id
    <root>/files/                    the original uploads, as <doc_id><ext>, plus <doc_id>.ocr.json
                                     (word positions of scanned pages, for highlighting)

Everything above is encrypted at rest (Phase 11, backend/security/): the files, OCR results and
vector index are sealed with AES-256-GCM, and so are the database's file names, passage text and
ID / policy numbers. The key is in the macOS Keychain, not in the vault folder. A vault made
before Phase 11 is encrypted in place the first time it is opened.

`root` defaults to data/index/ (git-ignored: it holds personal documents).

Each DocumentStore is one family member's view: what that person may see (Phase 12).
In family mode (the default, vault_meta "visibility" = "family") that is every document
except ones their owners marked private; in private mode, their own documents and those
shared with them. Either way only a document's owner can change, share or delete it. Views of the same vault share one database connection, vector index
and lock (the _Vault below); `store.for_user(user_id)` gives another member's view.

The FAISS index is exact (IndexFlatIP): a family has hundreds of pages, not
millions. Phase 13 can swap it for Qdrant behind the same methods.

Consistency: document rows and their chunks are written in one database
transaction, and the FAISS file is saved before that transaction commits. If
the two ever disagree (a crash between them), `missing_vectors` says so and the
app rebuilds the vectors from the stored chunk text on startup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import faiss
import numpy as np
from sqlalchemy import delete, func, insert, select

from backend.database.db import connect, fallback_owner
from backend.database.schema import document_chunks, document_shares, documents, users, vault_meta
from backend.parser.ocr import OcrPage, ocr_sidecar
from backend.security import keys
from backend.security.crypto import Cipher, DecryptError, is_sealed, new_key


logger = logging.getLogger(__name__)


FACT_FIELDS = ("expires_on", "due_on", "amount", "reference", "issuer", "facts_method")
DATE_FACTS = ("expires_on", "due_on")
SEALED_FACTS = ("reference", "issuer")  # PAN / Aadhaar / policy numbers and who issued them
KEY_CHECK = "document-vault"


def _fact_columns(document: dict, cipher: Cipher, fields=FACT_FIELDS) -> dict:
    """Phase 9 facts as database values (dates as date objects, numbers sealed)."""
    out = {}
    for f in fields:
        value = document.get(f)
        if f in DATE_FACTS and isinstance(value, str):
            value = date.fromisoformat(value)
        elif f in SEALED_FACTS:
            value = cipher.seal_text(value)
        out[f] = value
    return out


def top_results(ranked: list[tuple[dict, float]], top_k: int, per_document: bool) -> list[tuple[dict, float]]:
    """The first top_k of a ranking; per_document keeps only each document's best chunk."""
    results, seen = [], set()
    for chunk, score in ranked:
        if per_document:
            if chunk["doc_id"] in seen:
                continue
            seen.add(chunk["doc_id"])
        results.append((chunk, score))
        if len(results) == top_k:
            break
    return results


def _new_index(dim: int) -> faiss.Index:
    return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite drops the zone


def _default_url(root: Path) -> str:
    """Used when no database URL is given (tests, quick experiments): a SQLite file in the vault folder."""
    return f"sqlite:///{root / 'vault.db'}"


class _Vault:
    """What every family member's view shares: the database, the vector index (one file,
    all members' vectors), the files folder, and one lock around writes to them."""

    def __init__(self, root: Path, database_url: str):
        self.root = root
        self.files_dir = root / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.engine = connect(database_url)
        self.lock = threading.RLock()
        self.cipher, self.vault_id, self.key_is_new = self._unlock()
        index_path = root / "vectors.faiss"
        self.index: faiss.Index | None = None
        if index_path.exists():
            raw = self.cipher.open(index_path.read_bytes(), "index")
            self.index = faiss.deserialize_index(np.frombuffer(raw, dtype=np.uint8))
        with self.engine.connect() as conn:
            self.model = conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == "embedding_model")).scalar()
        self.views: dict[int, "DocumentStore"] = {}  # user id -> that member's view
        self.family_mode = self._meta("visibility") != "private"  # Phase 12: everyone sees everything

    def _meta(self, key: str) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == key)).scalar()

    def _put_meta(self, key: str, value: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(vault_meta).where(vault_meta.c.key == key))
            conn.execute(insert(vault_meta).values(key=key, value=value))

    def _unlock(self) -> tuple[Cipher, str, bool]:
        """This vault's cipher. A new vault (or one from before Phase 11) gets a new key, saved
        to the Keychain before anything is encrypted with it."""
        vault_id = self._meta("vault_id")
        if vault_id is None:
            vault_id = f"vault-{uuid.uuid4()}"
            self._put_meta("vault_id", vault_id)
        check = self._meta("key_check")
        key = keys.load(vault_id)
        if key is None:
            if check is not None:
                raise keys.KeyMissingError(
                    "This vault is encrypted and its key is not on this Mac. Put it back with your recovery key:\n"
                    "    python -m backend.security.cli restore-key")
            key = new_key()
            logger.info("New vault key saved to %s", keys.save(vault_id, key))
        cipher = Cipher(key)
        if check is not None:
            try:
                ok = cipher.open_text(check) == KEY_CHECK
            except DecryptError:
                ok = False
            if not ok:
                raise keys.KeyMissingError(
                    "The vault key on this Mac does not open this vault. Put the right one back with your recovery key:\n"
                    "    python -m backend.security.cli restore-key")
        return cipher, vault_id, check is None


class DocumentStore:
    def __init__(self, root: str | Path, database_url: str | None = None, owner: str | None = None,
                 *, _vault: _Vault | None = None, _user_id: int | None = None):
        if _vault is None:
            root = Path(root)
            _vault = _Vault(root, database_url or _default_url(root))
        self._vault = _vault
        self.root, self.files_dir, self.engine, self._lock = _vault.root, _vault.files_dir, _vault.engine, _vault.lock
        self.cipher = _vault.cipher
        if _user_id is None:
            self.owner = owner or self._owner_for_this_computer()
            self.owner_id = self._ensure_user(self.owner)
        else:
            with self.engine.connect() as conn:
                self.owner = conn.execute(select(users.c.name).where(users.c.id == _user_id)).scalar_one()
            self.owner_id = _user_id
        self.documents: dict[str, dict] = {}  # this owner's documents, cached
        self.chunks: list[dict] = []  # this owner's chunks, cached for search
        self.revision = 0  # bumped on every change, so derived indexes (BM25) know to rebuild
        with self._lock:
            _vault.views.setdefault(self.owner_id, self)
        self._migrate_json_vault()
        if _vault.key_is_new:
            self._encrypt_existing()
        self._load()

    def for_user(self, user_id: int) -> "DocumentStore":
        """Another family member's view of the same vault (created once, then reused)."""
        with self._lock:
            view = self._vault.views.get(user_id)
            if view is None:
                view = DocumentStore(self.root, _vault=self._vault, _user_id=user_id)
            return view

    @property
    def index(self) -> faiss.Index | None:
        return self._vault.index

    @index.setter
    def index(self, value: faiss.Index | None) -> None:
        self._vault.index = value

    @property
    def model(self) -> str | None:
        return self._vault.model

    @model.setter
    def model(self, value: str | None) -> None:
        self._vault.model = value

    # ---------- database ----------

    def _existing_owner(self) -> str | None:
        """The vault's owner when it has exactly one, so a renamed owner is remembered."""
        with self.engine.connect() as conn:
            names = conn.execute(select(users.c.name).limit(2)).scalars().all()
        return names[0] if len(names) == 1 else None

    def _owner_for_this_computer(self) -> str:
        """Owner when none is given: the vault's existing owner, or the computer's user name.

        An owner that was only ever named automatically after the computer's user follows that
        user name if it changes (Mac user "olduser" renamed to "newuser"). A name someone chose
        (Settings or the sign-in screen) is kept as is.
        """
        with self.engine.connect() as conn:  # after Phase 6 setup: the admin, never a new stranger
            admin = conn.execute(select(users.c.name).where(users.c.is_admin.is_(True), users.c.password_hash.is_not(None))
                                 .order_by(users.c.id).limit(1)).scalar()
        if admin:
            return admin
        computer = fallback_owner()
        existing = self._existing_owner()
        if existing is None or existing == computer:
            return existing or computer
        with self.engine.begin() as conn:
            if self._meta(conn, "owner_named_by") == "person":
                return existing
            if conn.execute(select(users.c.password_hash).where(users.c.name == existing)).scalar():
                return existing  # it's a sign-in name now; never change it behind the person's back
            if conn.execute(select(users.c.id).where(users.c.name == computer)).scalar() is not None:
                return existing  # the new name is already someone else here; don't merge people
            conn.execute(users.update().where(users.c.name == existing).values(name=computer))
        logger.info("Computer user name changed: vault owner %r is now %r", existing, computer)
        return computer

    def rename_owner(self, new_name: str) -> str:
        """Rename this vault's owner; their documents stay theirs."""
        new_name = " ".join(new_name.split())
        if not 1 <= len(new_name) <= 60:
            raise ValueError("the name must be 1 to 60 characters")
        with self._lock, self.engine.begin() as conn:
            taken = conn.execute(select(users.c.id).where(users.c.name == new_name, users.c.id != self.owner_id)).scalar()
            if taken is not None:
                raise ValueError(f"{new_name!r} is already another person in this vault")
            conn.execute(users.update().where(users.c.id == self.owner_id).values(name=new_name))
            self._set_meta(conn, "owner_named_by", "person")  # from now on, keep this name
        self.owner = new_name
        for doc in self.documents.values():
            if doc["mine"]:
                doc["owner"] = new_name
        for uid, view in list(self._vault.views.items()):  # "shared by <name>" elsewhere
            if view is not self:
                view._load()
        return new_name

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

    def _doc_row(self, document: dict, owner_id: int) -> dict:
        return {
            "id": document["doc_id"], "owner_id": owner_id, "name": self.cipher.seal_text(document["filename"]),
            "doc_type": document["category"], "uploaded_at": datetime.fromisoformat(document["added_at"]),
            "file_path": document["stored_as"], "pages": document["pages"], "ocr_pages": document.get("ocr_pages", 0),
            "ocr_confidence": document.get("ocr_confidence"),
            "category_source": document.get("category_source"),
            "category_confidence": document.get("category_confidence"),
            **_fact_columns(document, self.cipher),
            "num_chunks": document["num_chunks"], "size_bytes": document["size_bytes"], "sha256": document["sha256"],
        }

    def _chunk_row(self, chunk: dict, index_in_doc: int) -> dict:
        return {"id": chunk["vid"], "document_id": chunk["doc_id"], "page": chunk["page"],
                "chunk_index": index_in_doc, "text": self.cipher.seal_text(chunk["text"]), "n_tokens": chunk["n_tokens"]}

    def _load(self) -> None:
        open_text = self.cipher.open_text
        with self.engine.connect() as conn:
            shared_with_me = select(document_shares.c.document_id).where(document_shares.c.user_id == self.owner_id)
            visible = (documents.c.owner_id == self.owner_id) | documents.c.id.in_(shared_with_me)
            if self._vault.family_mode:
                visible = visible | documents.c.private.is_(False)
            direct = set(conn.execute(shared_with_me).scalars())
            rows = conn.execute(select(documents, users.c.name.label("owner_name"))
                                .join(users, users.c.id == documents.c.owner_id).where(visible)).mappings().all()
            recipients: dict[str, list[dict]] = {}
            for r in conn.execute(select(document_shares.c.document_id, users.c.id, users.c.name)
                                  .join(users, users.c.id == document_shares.c.user_id)
                                  .join(documents, documents.c.id == document_shares.c.document_id)
                                  .where(documents.c.owner_id == self.owner_id).order_by(users.c.name)).all():
                recipients.setdefault(r.document_id, []).append({"id": r.id, "name": r.name})
            self.documents = {
                r["id"]: {
                    "doc_id": r["id"], "filename": open_text(r["name"]), "category": r["doc_type"],
                    "owner": r["owner_name"], "owner_id": r["owner_id"], "mine": r["owner_id"] == self.owner_id,
                    # why this person sees it: their own, shared with them directly, or family mode
                    "via": "own" if r["owner_id"] == self.owner_id else "shared" if r["id"] in direct else "family",
                    "private": bool(r["private"]), "shared_with": recipients.get(r["id"], []),
                    "pages": r["pages"], "ocr_pages": r["ocr_pages"], "ocr_confidence": r["ocr_confidence"],
                    "category_source": r["category_source"], "category_confidence": r["category_confidence"],
                    **{f: (r[f].isoformat() if f in DATE_FACTS and r[f] else open_text(r[f]) if f in SEALED_FACTS else r[f])
                       for f in FACT_FIELDS},
                    "num_chunks": r["num_chunks"],
                    "size_bytes": r["size_bytes"], "sha256": r["sha256"], "stored_as": r["file_path"],
                    "added_at": _as_utc(r["uploaded_at"]).isoformat(timespec="seconds"),
                }
                for r in rows
            }
            chunk_rows = conn.execute(
                select(document_chunks)
                .join(documents, documents.c.id == document_chunks.c.document_id)
                .where(visible)
                .order_by(document_chunks.c.id)
            ).mappings().all()
        self.chunks = [
            {
                "vid": r["id"], "chunk_id": f"{r['document_id']}-p{r['page']}-{r['chunk_index']}",
                "doc_id": r["document_id"], "filename": self.documents[r["document_id"]]["filename"],
                "category": self.documents[r["document_id"]]["category"], "page": r["page"],
                "text": open_text(r["text"]), "n_tokens": r["n_tokens"],
            }
            for r in chunk_rows
        ]
        self.revision += 1

    def _refresh_others(self, user_ids=None, doc_id: str | None = None) -> None:
        """Other members' open views that show a changed document (or the given people) reload it."""
        for uid, view in list(self._vault.views.items()):
            if view is not self and ((user_ids and uid in user_ids) or (doc_id and doc_id in view.documents)):
                view._load()

    def _own(self, doc_id: str) -> dict:
        """A document this person may change: their own. Shared ones can only be looked at."""
        doc = self.documents.get(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        if not doc["mine"]:
            raise PermissionError(f"Only {doc['owner']} can change this document.")
        return doc

    def own_documents(self) -> list[dict]:
        return [d for d in self.documents.values() if d["mine"]]

    # ---------- sharing (Phase 12) ----------

    @property
    def family_mode(self) -> bool:
        return self._vault.family_mode

    def set_family_mode(self, on: bool) -> None:
        """Vault-wide: everyone sees every document not marked private (on), or only their own
        and those shared with them (off)."""
        with self._lock:
            self._vault._put_meta("visibility", "family" if on else "private")
            self._vault.family_mode = on
            for view in list(self._vault.views.values()):
                view._load()

    def set_private(self, doc_id: str, private: bool) -> dict:
        """Keep one of your documents to yourself even in family mode (people you shared it
        with directly still see it)."""
        with self._lock:
            doc = self._own(doc_id)
            with self.engine.begin() as conn:
                conn.execute(documents.update().where(documents.c.id == doc_id).values(private=private))
            doc["private"] = private
            for view in list(self._vault.views.values()):
                if view is not self:
                    view._load()
            return doc

    def share(self, doc_id: str, user_ids: list[int]) -> dict:
        """Share one of your documents with exactly these family members (an empty list: nobody)."""
        with self._lock:
            doc = self._own(doc_id)
            wanted = {int(u) for u in user_ids} - {self.owner_id}
            with self.engine.begin() as conn:
                known = set(conn.execute(select(users.c.id).where(users.c.id.in_(wanted))).scalars()) if wanted else set()
                if wanted - known:
                    raise ValueError("No such family member.")
                before = set(conn.execute(select(document_shares.c.user_id)
                                          .where(document_shares.c.document_id == doc_id)).scalars())
                conn.execute(delete(document_shares).where(document_shares.c.document_id == doc_id,
                                                           document_shares.c.user_id.not_in(wanted)))
                now = datetime.now(timezone.utc)
                for uid in wanted - before:
                    conn.execute(insert(document_shares).values(document_id=doc_id, user_id=uid, shared_at=now))
                names = conn.execute(select(users.c.id, users.c.name).where(users.c.id.in_(wanted))
                                     .order_by(users.c.name)).all() if wanted else []
            doc["shared_with"] = [{"id": r.id, "name": r.name} for r in names]
            self._refresh_others(user_ids=before ^ wanted)
            self.revision += 1
            return doc

    def leave(self, doc_id: str) -> bool:
        """Take a document someone shared with you off your list (theirs is untouched). Not for
        documents seen through family mode: those are everyone's to see."""
        with self._lock:
            doc = self.documents.get(doc_id)
            if doc is None or doc["via"] != "shared":
                return False
            with self.engine.begin() as conn:
                conn.execute(delete(document_shares).where(document_shares.c.document_id == doc_id,
                                                           document_shares.c.user_id == self.owner_id))
            self._load()
            owner_view = self._vault.views.get(doc["owner_id"])
            if owner_view is not None:
                owner_view._load()
            return True

    def _save_index(self) -> None:
        index_path = self.root / "vectors.faiss"
        if self.index is not None and self.index.ntotal:
            tmp = self.root / "vectors.faiss.tmp"
            tmp.write_bytes(self.cipher.seal(faiss.serialize_index(self.index).tobytes(), "index"))
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
                self.model = json.loads(meta_path.read_text())["model"]
                self._set_meta(conn, "embedding_model", self.model)
        for path in (docs_path, chunks_path, self.root / "meta.json"):
            if path.exists():
                path.rename(path.with_name(path.name + ".migrated"))

    # ---------- reads ----------

    @property
    def num_vectors(self) -> int:
        return self.index.ntotal if self.index is not None else 0

    @property
    def missing_vectors(self) -> bool:
        """True if some stored chunk (anyone's) has no vector (e.g. a crash mid-save); rebuild with reembed()."""
        with self.engine.connect() as conn:
            all_vids = conn.execute(select(document_chunks.c.id)).scalars().all()
        if not all_vids:
            return False
        if self.index is None:
            return True
        vids = np.array(all_vids, dtype=np.int64)
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
        """Where the (encrypted) file is kept. Use read_file or plain_copy to get at its contents."""
        doc = self.documents.get(doc_id)
        return self.files_dir / doc["stored_as"] if doc else None

    def read_file(self, doc_id: str) -> bytes | None:
        path = self.file_path(doc_id)
        if path is None or not path.exists():
            return None
        return self.cipher.open(path.read_bytes(), "file")

    @contextmanager
    def plain_copy(self, doc_id: str):
        """A decrypted copy of a document (and its OCR results) in a private temporary folder,
        for the PDF / image libraries that need a path; deleted as soon as the block ends.
        OCR results worked out meanwhile (a page read for the first time) are sealed and kept."""
        path = self.file_path(doc_id)
        if path is None or not path.exists():
            yield None
            return
        folder = Path(tempfile.mkdtemp(prefix="vault-"))  # mode 0700: only this user
        try:
            plain = folder / path.name
            plain.write_bytes(self.cipher.open(path.read_bytes(), "file"))
            sidecar = ocr_sidecar(path)
            before = self.cipher.open(sidecar.read_bytes(), "ocr") if sidecar.exists() else None
            if before is not None:
                ocr_sidecar(plain).write_bytes(before)
            yield plain
            after = ocr_sidecar(plain).read_bytes() if ocr_sidecar(plain).exists() else None
            if after is not None and after != before:
                with self._lock:  # two page views of one scan at once
                    self._write_sealed(sidecar, after, "ocr")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _write_sealed(self, path: Path, data: bytes, kind: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(self.cipher.seal(data, kind))
        os.replace(tmp, path)

    def put_file(self, stored_as: str, source: Path) -> None:
        """Keep an upload in the vault, encrypted."""
        self._write_sealed(self.files_dir / stored_as, Path(source).read_bytes(), "file")

    def put_ocr(self, stored_as: str, pages: dict[int, OcrPage]) -> None:
        raw = json.dumps({str(n): p.to_dict() for n, p in sorted(pages.items())}).encode("utf-8")
        self._write_sealed(ocr_sidecar(self.files_dir / stored_as), raw, "ocr")

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        category: str | None = None,
        per_document: bool = True,
    ) -> list[tuple[dict, float]]:
        """[(chunk, score), ...] best first, by vector similarity only (see backend/retriever/
        for hybrid search).

        per_document=True keeps only each document's best chunk (a results list);
        False returns the top chunks regardless of document (context for the LLM).
        """
        return top_results(self.rank(query_embedding, category), top_k, per_document)

    def rank(self, query_embedding: np.ndarray, category: str | None = None) -> list[tuple[dict, float]]:
        """Every one of this owner's chunks (optionally one category), most similar first."""
        with self._lock:
            if not self.num_vectors or not self.chunks:
                return []
            # Rank everything: exact search over a family-sized vault takes microseconds,
            # and filtering by owner/category after a short top-k could leave too few results.
            query = np.ascontiguousarray(query_embedding.reshape(1, -1), dtype=np.float32)
            scores, vids = self.index.search(query, self.num_vectors)
            by_vid = {c["vid"]: c for c in self.chunks}  # only this owner's chunks
            results = []
            for score, vid in zip(scores[0], vids[0]):
                chunk = by_vid.get(int(vid))
                if chunk is not None and not (category and chunk["category"] != category):
                    results.append((chunk, float(score)))
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
            self.documents[document["doc_id"]] = {**document, "owner": self.owner, "owner_id": self.owner_id,
                                                  "mine": True, "via": "own", "private": False, "shared_with": []}
            self.chunks.extend(chunks)
            self.revision += 1
        if self.family_mode:  # the rest of the family sees it too
            for view in list(self._vault.views.values()):
                if view is not self:
                    view._load()

    def update_category(self, doc_id: str, category: str, vectors: np.ndarray | None = None,
                        source: str = "person", confidence: int | None = 100) -> dict:
        """Re-file a document under another type. `vectors` are its chunks' new embeddings
        (the category is part of the embedded text), in the order of `chunks_of(doc_id)`.
        source="person": someone chose it, so automatic re-sorting leaves it alone from now on."""
        with self._lock:
            doc = self._own(doc_id)
            with self.engine.begin() as conn:
                conn.execute(documents.update().where(documents.c.id == doc_id).values(
                    doc_type=category, category_source=source, category_confidence=confidence))
                chunks = self.chunks_of(doc_id)
                if vectors is not None and chunks and self.index is not None:
                    vids = np.array([c["vid"] for c in chunks], dtype=np.int64)
                    self.index.remove_ids(vids)
                    self.index.add_with_ids(np.ascontiguousarray(vectors, dtype=np.float32), vids)
                    self._save_index()
            doc.update(category=category, category_source=source, category_confidence=confidence)
            for c in self.chunks:
                if c["doc_id"] == doc_id:
                    c["category"] = category
            self.revision += 1
            self._refresh_others(doc_id=doc_id)
            return doc

    def update_facts(self, doc_id: str, facts: dict, method: str) -> dict:
        """Set a document's key facts (Phase 9). method="person" when someone corrected them."""
        with self._lock:
            doc = self._own(doc_id)
            values = {f: facts.get(f) for f in FACT_FIELDS if f != "facts_method" and f in facts}
            values["facts_method"] = method
            with self.engine.begin() as conn:
                conn.execute(documents.update().where(documents.c.id == doc_id)
                             .values(**_fact_columns(values, self.cipher, fields=values.keys())))
            doc.update(values)
            self._refresh_others(doc_id=doc_id)
            return doc

    def chunks_of(self, doc_id: str) -> list[dict]:
        return [c for c in self.chunks if c["doc_id"] == doc_id]

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.documents.get(doc_id)
            if doc is None:
                return False
            if not doc["mine"]:
                raise PermissionError(f"Only {doc['owner']} can delete this document.")
            removed = [c["vid"] for c in self.chunks if c["doc_id"] == doc_id]
            with self.engine.begin() as conn:
                conn.execute(delete(document_shares).where(document_shares.c.document_id == doc_id))
                conn.execute(delete(document_chunks).where(document_chunks.c.document_id == doc_id))
                conn.execute(delete(documents).where(documents.c.id == doc_id))
            # Vectors go after the rows: a leftover vector with no row is ignored by search.
            if removed and self.index is not None:
                self.index.remove_ids(np.array(removed, dtype=np.int64))
                self._save_index()
            del self.documents[doc_id]
            self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id]
            self.revision += 1
            self._refresh_others(doc_id=doc_id)
            (self.files_dir / doc["stored_as"]).unlink(missing_ok=True)
            ocr_sidecar(self.files_dir / doc["stored_as"]).unlink(missing_ok=True)
            return True

    def reembed(self, embed_passages, model: str) -> None:
        """Rebuild every family member's vectors (new model, or repair) from the stored chunk text;
        no re-parsing. The index is shared, so all members' chunks go back in, not just this view's."""
        with self._lock:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    select(document_chunks.c.id, document_chunks.c.text, documents.c.name, documents.c.doc_type)
                    .join(documents, documents.c.id == document_chunks.c.document_id)
                    .order_by(document_chunks.c.id)
                ).all()
            open_text = self.cipher.open_text
            everyone = [{"vid": r.id, "text": open_text(r.text), "filename": open_text(r.name), "category": r.doc_type}
                        for r in rows]
            self.index = None
            if everyone:
                vectors = np.ascontiguousarray(embed_passages(everyone), dtype=np.float32)
                self.index = _new_index(vectors.shape[1])
                self.index.add_with_ids(vectors, np.array([c["vid"] for c in everyone], dtype=np.int64))
            self._save_index()
            with self.engine.begin() as conn:
                self._set_meta(conn, "embedding_model", model)
            self.model = model

    # ---------- encryption of a vault made before Phase 11 ----------

    def _encrypt_existing(self) -> None:
        """Seal everything written before encryption was switched on, then record the key check.
        Safe to repeat after an interruption: sealed values are left as they are."""
        seal = self.cipher.seal_text
        with self._lock:
            with self.engine.begin() as conn:
                for r in conn.execute(select(documents.c.id, documents.c.name, documents.c.reference,
                                             documents.c.issuer)).all():
                    conn.execute(documents.update().where(documents.c.id == r.id).values(
                        name=seal(r.name), reference=seal(r.reference), issuer=seal(r.issuer)))
                for r in conn.execute(select(document_chunks.c.id, document_chunks.c.text)).all():
                    conn.execute(document_chunks.update().where(document_chunks.c.id == r.id).values(text=seal(r.text)))
            count = 0
            for path in sorted(self.files_dir.iterdir()):
                if not path.is_file() or path.name.endswith(".tmp"):
                    continue
                data = path.read_bytes()
                if not is_sealed(data):
                    self._write_sealed(path, data, "ocr" if path.name.endswith(".ocr.json") else "file")
                    count += 1
            index_path = self.root / "vectors.faiss"
            if index_path.exists() and not is_sealed(index_path.read_bytes()):
                self._save_index()
            self._vault._put_meta("key_check", self.cipher.seal_text(KEY_CHECK))
            self._vault.key_is_new = False
        if count:
            logger.info("Encrypted %d existing files in the vault", count)
        logger.warning("The vault is encrypted; its key is in %s. Write down the recovery key once: "
                       "python -m backend.security.cli recovery-key", keys.where(self._vault.vault_id))
