"""A document, person, paired device or shared setting as one computer sends it to another, and putting one in place.

A document travels whole: its details (decrypted, over the encrypted, certificate-checked connection between the two
computers), its searchable passages and their search vectors, who owns it and who it is shared with. Its file and
OCR results travel separately, still sealed with the vault key both computers have. People are matched by name (each
computer numbers them itself); devices by the SHA-256 of their key.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone

import numpy as np
from sqlalchemy import delete, insert, select

from backend.database.schema import devices, document_chunks, document_shares, documents, users, vault_meta
from backend.parser.ocr import ocr_sidecar
from backend.sync import stamps


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return value.isoformat()


# ---------- documents ----------

def export_doc(store, doc_id: str) -> dict | None:
    """Everything about one document except its file (see doc_files)."""
    from backend.vector_store.store import DATE_FACTS, FACT_FIELDS, SEALED_FACTS

    open_text = store.cipher.open_text
    with store._lock, store.engine.connect() as conn:
        row = conn.execute(select(documents, users.c.name.label("owner_name"))
                           .join(users, users.c.id == documents.c.owner_id).where(documents.c.id == doc_id)).mappings().first()
        if row is None:
            return None
        chunks = conn.execute(select(document_chunks).where(document_chunks.c.document_id == doc_id)
                              .order_by(document_chunks.c.chunk_index)).mappings().all()
        shared = conn.execute(select(users.c.name).join(document_shares, document_shares.c.user_id == users.c.id)
                              .where(document_shares.c.document_id == doc_id)).scalars().all()
        vectors = []
        if store.index is not None:
            for c in chunks:
                try:
                    vectors.append(store.index.reconstruct(int(c["id"])))
                except RuntimeError:  # no vector (an interrupted save): the other computer makes it
                    vectors = []
                    break
    facts = {f: (_iso(row[f]) if f in DATE_FACTS else open_text(row[f]) if f in SEALED_FACTS else row[f]) for f in FACT_FIELDS}
    return {
        "doc_id": row["id"], "owner": row["owner_name"], "filename": open_text(row["name"]), "category": row["doc_type"],
        "added_at": _iso(row["uploaded_at"]), "stored_as": row["file_path"], "pages": row["pages"],
        "ocr_pages": row["ocr_pages"], "ocr_confidence": row["ocr_confidence"],
        "category_source": row["category_source"], "category_confidence": row["category_confidence"], **facts,
        "num_chunks": row["num_chunks"], "size_bytes": row["size_bytes"], "sha256": row["sha256"],
        "private": bool(row["private"]), "shared_with": sorted(shared),
        "chunks": [{"page": c["page"], "chunk_index": c["chunk_index"], "text": open_text(c["text"]), "n_tokens": c["n_tokens"]}
                   for c in chunks],
        "model": store.model,
        "vectors": base64.b64encode(np.ascontiguousarray(vectors, dtype=np.float32).tobytes()).decode() if vectors else None,
    }


def doc_files(store, stored_as: str) -> dict[str, bytes]:
    """The document's file and OCR results as kept here: sealed with the vault key."""
    path = store.files_dir / stored_as
    out = {}
    if path.exists():
        out["file"] = path.read_bytes()
    if ocr_sidecar(path).exists():
        out["ocr"] = ocr_sidecar(path).read_bytes()
    return out


def _user_id(conn, name: str) -> int:
    found = conn.execute(select(users.c.id).where(users.c.name == name)).scalar()
    if found is None:
        found = conn.execute(insert(users).values(name=name, created_at=datetime.now(timezone.utc))
                             .returning(users.c.id)).scalar_one()
    return found


def apply_doc(store, record: dict, files: dict[str, bytes], stamp: dict, embed=None) -> None:
    """Put another computer's version of a document in place (replacing this computer's), with its stamp.
    `embed(chunks)`: search vectors for chunks, when the other computer's can't be used (another search model)."""
    from backend.vector_store.store import _new_index

    with store._lock:
        vectors = None
        if record.get("vectors") and (store.model in (None, record.get("model"))):
            vectors = np.frombuffer(base64.b64decode(record["vectors"]), dtype=np.float32)
            vectors = vectors.reshape(len(record["chunks"]), -1) if record["chunks"] else None
        if vectors is None and record["chunks"]:
            if embed is None:
                raise ValueError("this document's search vectors can't be used here")
            vectors = np.ascontiguousarray(embed([{**c, "filename": record["filename"], "category": record["category"]}
                                                  for c in record["chunks"]]), dtype=np.float32)
        for name, data in files.items():  # the files first: a document row never points at a missing file
            path = store.files_dir / record["stored_as"]
            target = path if name == "file" else ocr_sidecar(path)
            tmp = target.with_name(target.name + ".sync")
            tmp.write_bytes(data)
            tmp.replace(target)
        with store.engine.begin() as conn:
            old_vids = conn.execute(select(document_chunks.c.id).where(document_chunks.c.document_id == record["doc_id"])).scalars().all()
            conn.execute(delete(document_shares).where(document_shares.c.document_id == record["doc_id"]))
            conn.execute(delete(document_chunks).where(document_chunks.c.document_id == record["doc_id"]))
            conn.execute(delete(documents).where(documents.c.id == record["doc_id"]))
            owner_id = _user_id(conn, record["owner"])
            first = store._next_vid(conn)
            chunks = [{**c, "vid": first + i, "doc_id": record["doc_id"]} for i, c in enumerate(record["chunks"])]
            conn.execute(insert(documents).values(**{**store._doc_row(record, owner_id), "private": record.get("private", False)}))
            if chunks:
                conn.execute(insert(document_chunks), [store._chunk_row(c, c["chunk_index"]) for c in chunks])
            for name in record.get("shared_with", []):
                conn.execute(insert(document_shares).values(document_id=record["doc_id"], user_id=_user_id(conn, name),
                                                            shared_at=datetime.now(timezone.utc)))
            if store.model is None and record.get("model"):
                store._set_meta(conn, "embedding_model", record["model"])
                store.model = record["model"]
            if old_vids and store.index is not None:
                store.index.remove_ids(np.array(old_vids, dtype=np.int64))
            if chunks:
                if store.index is None:
                    store.index = _new_index(vectors.shape[1])
                store.index.add_with_ids(np.ascontiguousarray(vectors, dtype=np.float32),
                                         np.array([c["vid"] for c in chunks], dtype=np.int64))
            store._save_index()  # before commit: if this fails, the rows roll back
            stamps.put(conn, "doc", record["doc_id"], stamp["changed_at"], stamp["changed_by"])
        _reload_everyone(store)


def remove_doc(store, doc_id: str, stamp: dict) -> None:
    """Another computer deleted this document: delete it here too, and remember it (so it isn't brought back)."""
    with store._lock:
        with store.engine.begin() as conn:
            row = conn.execute(select(documents.c.file_path).where(documents.c.id == doc_id)).first()
            vids = conn.execute(select(document_chunks.c.id).where(document_chunks.c.document_id == doc_id)).scalars().all()
            conn.execute(delete(document_shares).where(document_shares.c.document_id == doc_id))
            conn.execute(delete(document_chunks).where(document_chunks.c.document_id == doc_id))
            conn.execute(delete(documents).where(documents.c.id == doc_id))
            stamps.put(conn, "doc", doc_id, stamp["changed_at"], stamp["changed_by"], gone=True)
        if vids and store.index is not None:
            store.index.remove_ids(np.array(vids, dtype=np.int64))
            store._save_index()
        if row is not None:
            (store.files_dir / row.file_path).unlink(missing_ok=True)
            ocr_sidecar(store.files_dir / row.file_path).unlink(missing_ok=True)
        _reload_everyone(store)


def _reload_everyone(store) -> None:
    store.revision += 1
    for view in list(store._vault.views.values()):
        view._load()


# ---------- people ----------

def export_user(engine, name: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(users).where(users.c.name == name)).mappings().first()
        was = conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == f"renamed:{name}"[:64])).scalar()
    if row is None:
        return None
    return {"name": row["name"], "email": row["email"], "password_hash": row["password_hash"],
            "is_admin": bool(row["is_admin"]), "created_at": _iso(row["created_at"]), "was": was}


def apply_user(engine, record: dict, stamp: dict) -> None:
    """The same person as on the other computer: their sign-in, and whether they're an admin. A renamed person
    is renamed here too (their documents stay theirs)."""
    values = {"email": record.get("email"), "password_hash": record.get("password_hash"), "is_admin": record["is_admin"]}
    with engine.begin() as conn:
        found = conn.execute(select(users.c.id).where(users.c.name == record["name"])).scalar()
        if found is None and record.get("was"):
            found = conn.execute(select(users.c.id).where(users.c.name == record["was"])).scalar()
        if found is None:
            created = datetime.fromisoformat(record["created_at"]) if record.get("created_at") else datetime.now(timezone.utc)
            conn.execute(insert(users).values(name=record["name"], created_at=created, **values))
        else:
            conn.execute(users.update().where(users.c.id == found).values(name=record["name"], **values))
        stamps.put(conn, "user", record["name"], stamp["changed_at"], stamp["changed_by"])


# ---------- paired laptops and phones ----------

def export_device(engine, token_hash: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(select(devices, users.c.name.label("user_name")).join(users, users.c.id == devices.c.user_id)
                           .where(devices.c.token_hash == token_hash)).mappings().first()
    if row is None:
        return None
    return {"token_hash": row["token_hash"], "user": row["user_name"], "name": row["name"], "created_at": _iso(row["created_at"])}


def apply_device(engine, record: dict, stamp: dict) -> None:
    """A laptop or phone paired with another computer that keeps this vault works with this one too."""
    with engine.begin() as conn:
        conn.execute(delete(devices).where(devices.c.token_hash == record["token_hash"]))
        conn.execute(insert(devices).values(
            user_id=_user_id(conn, record["user"]), name=record["name"], token_hash=record["token_hash"],
            created_at=datetime.fromisoformat(record["created_at"]) if record.get("created_at") else datetime.now(timezone.utc)))
        stamps.put(conn, "device", record["token_hash"], stamp["changed_at"], stamp["changed_by"])


def remove_device(engine, token_hash: str, stamp: dict) -> None:
    with engine.begin() as conn:
        conn.execute(delete(devices).where(devices.c.token_hash == token_hash))
        stamps.put(conn, "device", token_hash, stamp["changed_at"], stamp["changed_by"], gone=True)


# ---------- shared settings ----------

def export_meta(engine, key: str) -> dict | None:
    if key not in stamps.SYNCED_META:
        return None
    with engine.connect() as conn:
        value = conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == key)).scalar()
    return {"key": key, "value": value}


def apply_meta(store, record: dict, stamp: dict) -> None:
    if record["key"] not in stamps.SYNCED_META or record.get("value") is None:
        return
    with store._lock:
        with store.engine.begin() as conn:
            store._set_meta(conn, record["key"], record["value"])
            stamps.put(conn, "meta", record["key"], stamp["changed_at"], stamp["changed_by"])
        if record["key"] == "visibility":
            store._vault.family_mode = record["value"] != "private"
        _reload_everyone(store)


__all__ = ["apply_device", "apply_doc", "apply_meta", "apply_user", "doc_files", "export_device", "export_doc",
           "export_meta", "export_user", "remove_device", "remove_doc", "date"]
