"""When each document, person, device or setting last changed, and on which computer: the newest change wins.

Every change on this computer stamps what it changed (inside the same transaction). A change copied from another
computer keeps that computer's stamp, so it isn't sent back as new.
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import delete, insert, select

from backend.database.schema import document_chunks, documents, sync_stamps, users, vault_meta

KINDS = ("doc", "user", "device", "meta")
SYNCED_META = ("visibility",)  # settings everyone shares (family mode)


def centre_id(conn) -> str:
    """This computer's id among the computers that keep the vault (made the first time it's asked for)."""
    found = conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == "centre_id")).scalar()
    if found is None:
        found = f"centre-{uuid.uuid4()}"
        conn.execute(insert(vault_meta).values(key="centre_id", value=found))
    return found


def _now(conn, kind: str, key: str) -> float:
    """Now, but always after this thing's last stamp (clocks on two computers can disagree a little)."""
    before = conn.execute(select(sync_stamps.c.changed_at).where(sync_stamps.c.kind == kind,
                                                                  sync_stamps.c.key == key)).scalar()
    now = time.time()
    return max(now, before + 0.001) if before is not None else now


def put(conn, kind: str, key: str, changed_at: float, changed_by: str, gone: bool = False) -> None:
    conn.execute(delete(sync_stamps).where(sync_stamps.c.kind == kind, sync_stamps.c.key == key))
    conn.execute(insert(sync_stamps).values(kind=kind, key=key, changed_at=changed_at, changed_by=changed_by, gone=gone))


def stamp(conn, kind: str, key: str, gone: bool = False) -> None:
    """Something changed (or was deleted) on this computer."""
    put(conn, kind, key, _now(conn, kind, key), centre_id(conn), gone)


def get(conn, kind: str, key: str) -> dict | None:
    row = conn.execute(select(sync_stamps).where(sync_stamps.c.kind == kind, sync_stamps.c.key == key)).mappings().first()
    return dict(row) if row else None


def newer(theirs: dict, ours: dict | None) -> bool:
    """Is their change newer than ours? Ties (the same moment on two computers) go to the higher computer id, so
    both computers decide the same way."""
    if ours is None:
        return True
    return (theirs["changed_at"], theirs["changed_by"]) > (ours["changed_at"], ours["changed_by"])


def manifest(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(select(sync_stamps)).mappings()]


def backfill(conn) -> int:
    """Stamp what was in the vault before it was kept on several computers (documents, people, settings), so a
    computer that joins gets all of it. Things already stamped are left alone."""
    me = centre_id(conn)
    have = {(r.kind, r.key) for r in conn.execute(select(sync_stamps.c.kind, sync_stamps.c.key))}
    added = 0
    for doc_id, uploaded in conn.execute(select(documents.c.id, documents.c.uploaded_at)):
        if ("doc", doc_id) not in have:
            put(conn, "doc", doc_id, uploaded.timestamp() if uploaded else time.time(), me)
            added += 1
    for name, created in conn.execute(select(users.c.name, users.c.created_at)):
        if ("user", name) not in have:
            put(conn, "user", name, created.timestamp() if created else time.time(), me)
            added += 1
    from backend.database.schema import devices

    for token_hash, created in conn.execute(select(devices.c.token_hash, devices.c.created_at)):
        if ("device", token_hash) not in have:
            put(conn, "device", token_hash, created.timestamp() if created else time.time(), me)
            added += 1
    for key in SYNCED_META:
        if ("meta", key) not in have and conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == key)).scalar():
            put(conn, "meta", key, time.time(), me)
            added += 1
    return added


__all__ = ["KINDS", "backfill", "centre_id", "get", "manifest", "newer", "put", "stamp", "document_chunks"]
