"""Phase 11: encryption at rest, the vault key, encrypted backups, and the access log."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.database.schema import access_log, document_chunks, documents, vault_meta
from backend.security import crypto, keys
from backend.security.backup import MAGIC as BACKUP_MAGIC
from backend.security.backup import BackupError, make_backup, restore_backup
from backend.security.crypto import Cipher, DecryptError, key_from_recovery_code, recovery_code
from backend.upload.ingest import ingest_file
from backend.vector_store import store as store_module
from backend.vector_store.store import DocumentStore
from tests.fakes import PASSWORD, FakeEmbedder, signed_in

TOK = RegexTokenizer()
SECRET = "Two-wheeler insurance. Policy number TW-778812. PAN ABCDE1234F. Expires 14 March 2027."


def add(store, tmp_path, name="bike_policy.txt", text=SECRET):
    path = tmp_path / name
    path.write_text(text)
    return ingest_file(path, store, FakeEmbedder(), TOK).document


def everything_on_disk(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file() and p.suffix != ".db")


def test_cipher_round_trips_and_refuses_changes():
    c = Cipher(crypto.new_key())
    sealed = c.seal(b"aadhaar card", "file")
    assert crypto.is_sealed(sealed) and b"aadhaar" not in sealed and c.open(sealed, "file") == b"aadhaar card"
    with pytest.raises(DecryptError):
        c.open(sealed[:-1] + bytes([sealed[-1] ^ 1]), "file")  # one bit changed
    with pytest.raises(DecryptError):
        c.open(sealed, "ocr")  # moved to another kind of slot
    assert c.open_text(c.seal_text("2345 6789 0124")) == "2345 6789 0124"
    assert c.open(b"%PDF plain") == b"%PDF plain" and c.open_text("plain") == "plain"  # pre-Phase-11 values


def test_recovery_code_survives_being_written_down():
    key = crypto.new_key()
    code = recovery_code(key)
    assert len(code.split("-")) == 13
    assert key_from_recovery_code(code.lower().replace("-", " ")) == key
    with pytest.raises(ValueError):
        key_from_recovery_code(code[:-5])


def test_nothing_readable_is_left_on_disk_or_in_the_database(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="arjun")
    doc = add(store, tmp_path)
    disk = everything_on_disk(tmp_path / "index")
    for secret in (b"TW-778812", b"ABCDE1234F", b"Two-wheeler", b"bike_policy"):
        assert secret not in disk
    assert (tmp_path / "index" / "vectors.faiss").read_bytes().startswith(crypto.MAGIC)
    with store.engine.connect() as conn:
        row = conn.execute(select(documents)).mappings().one()
        texts = conn.execute(select(document_chunks.c.text)).scalars().all()
    assert all(v.startswith("dv1:") for v in [row["name"], row["reference"], *texts])
    # ...and the key is not in the vault folder
    assert not any(p.suffix == ".key" for p in (tmp_path / "index").rglob("*"))
    assert store.read_file(doc["doc_id"]) == SECRET.encode()

    reopened = DocumentStore(tmp_path / "index", owner="arjun")
    again = reopened.get(doc["doc_id"])
    assert again["filename"] == "bike_policy.txt" and again["reference"] and "TW" in again["reference"]
    assert any("ABCDE1234F" in c["text"] for c in reopened.chunks) and reopened.num_vectors == store.num_vectors


def test_a_changed_file_is_refused_not_misread(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="arjun")
    doc = add(store, tmp_path)
    path = store.file_path(doc["doc_id"])
    data = bytearray(path.read_bytes())
    data[-3] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(DecryptError):
        store.read_file(doc["doc_id"])


def test_without_its_key_the_vault_does_not_open_until_the_recovery_key_is_given(tmp_path, monkeypatch):
    store = DocumentStore(tmp_path / "index", owner="arjun")
    doc = add(store, tmp_path)
    vault_id = store._vault.vault_id
    code = recovery_code(keys.load(vault_id))

    monkeypatch.setenv("VAULT_KEY_DIR", str(tmp_path / "new-mac-keys"))  # a Mac without the key
    with pytest.raises(keys.KeyMissingError, match="recovery key"):
        DocumentStore(tmp_path / "index", owner="arjun")
    keys.save(vault_id, crypto.new_key())  # someone else's key
    with pytest.raises(keys.KeyMissingError, match="does not open"):
        DocumentStore(tmp_path / "index", owner="arjun")

    keys.save(vault_id, key_from_recovery_code(code))
    assert DocumentStore(tmp_path / "index", owner="arjun").read_file(doc["doc_id"]) == SECRET.encode()


class _NoEncryption(Cipher):
    """How the vault wrote things before Phase 11."""

    def seal(self, data, kind="file"):
        return data

    def seal_text(self, value):
        return value


def test_a_vault_from_before_phase_11_is_encrypted_in_place(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(store_module, "Cipher", _NoEncryption)
        old = DocumentStore(tmp_path / "index", owner="arjun")
        doc = add(old, tmp_path)
        scan = tmp_path / "index" / "files" / (Path(doc["stored_as"]).stem + ".ocr.json")
        scan.write_text('{"1": {"text": "PAN ABCDE1234F", "width": 10, "height": 10, "rotation": 0, "confidence": 90, "words": []}}')
    assert b"ABCDE1234F" in everything_on_disk(tmp_path / "index")
    with old.engine.begin() as conn:  # a pre-Phase-11 vault has no key check and no key
        conn.execute(delete(vault_meta).where(vault_meta.c.key == "key_check"))
    (Path(os.environ["VAULT_KEY_DIR"]) / f"{old._vault.vault_id}.key").unlink()
    old.engine.dispose()

    store = DocumentStore(tmp_path / "index", owner="arjun")
    assert b"ABCDE1234F" not in everything_on_disk(tmp_path / "index")
    assert b"TW-778812" not in everything_on_disk(tmp_path / "index")
    with store.engine.connect() as conn:
        assert conn.execute(select(documents.c.reference)).scalar().startswith("dv1:")
    assert store.read_file(doc["doc_id"]) == SECRET.encode()
    assert store.get(doc["doc_id"])["filename"] == "bike_policy.txt" and store.num_vectors > 0
    with store.plain_copy(doc["doc_id"]) as plain:
        assert "ABCDE1234F" in (plain.parent / (plain.stem + ".ocr.json")).read_text()
    assert not plain.parent.exists()  # the decrypted copy is gone afterwards


def test_a_backup_restores_on_a_mac_that_never_saw_the_vault(tmp_path, monkeypatch):
    store = DocumentStore(tmp_path / "index", owner="arjun")
    doc = add(store, tmp_path)
    backup = make_backup(store, tmp_path / "usb", "correct horse battery", n=2 ** 14)
    assert backup.read_bytes().startswith(BACKUP_MAGIC) and b"TW-778812" not in backup.read_bytes()
    with pytest.raises(BackupError, match="at least"):
        make_backup(store, tmp_path / "usb", "short")

    monkeypatch.setenv("VAULT_KEY_DIR", str(tmp_path / "new-mac-keys"))
    with pytest.raises(BackupError, match="Wrong backup password"):
        restore_backup(backup, "wrong password!", tmp_path / "new")
    damaged = tmp_path / "damaged.dvbackup"
    damaged.write_bytes(backup.read_bytes()[:-40])
    with pytest.raises(BackupError):
        restore_backup(damaged, "correct horse battery", tmp_path / "new")

    done = restore_backup(backup, "correct horse battery", tmp_path / "new")
    assert done["documents"] == 1
    restored = DocumentStore(tmp_path / "new", owner="arjun")
    assert restored.read_file(doc["doc_id"]) == SECRET.encode()
    assert restored.get(doc["doc_id"])["reference"] == store.get(doc["doc_id"])["reference"]
    assert restored.num_vectors == store.num_vectors
    add(restored, tmp_path, "second.txt", "Electricity bill. Amount due Rs. 2,486.")  # ids carry on
    assert len(restored.list_documents()) == 2

    with pytest.raises(BackupError, match="already has documents"):
        restore_backup(backup, "correct horse battery", tmp_path / "new")


def test_the_access_log_records_who_did_what(tmp_path):
    store = DocumentStore(tmp_path / "index")
    app = create_app(store, FakeEmbedder(), TOK, frontend_dir=None)
    client = signed_in(app, "abhinav")
    doc = client.post("/api/documents", files=[("files", ("bike_policy.txt", SECRET.encode()))]).json()[0]["document"]
    opened = client.get(f"/api/documents/{doc['doc_id']}/file")
    assert opened.content == SECRET.encode() and opened.headers["cache-control"] == "private, no-store"
    client.get(f"/api/documents/{doc['doc_id']}/file")  # the same opening, not logged twice
    client.delete(f"/api/documents/{doc['doc_id']}")
    client.post("/api/users", json={"name": "sunita", "password": "another long password"})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"name": "abhinav", "password": "not it at all"}).status_code == 401
    client.post("/api/auth/login", json={"name": "abhinav", "password": PASSWORD})

    mine = client.get("/api/activity").json()["entries"]
    actions = [e["action"] for e in reversed(mine)]
    assert actions == ["sign_in", "document_added", "document_opened", "document_deleted", "member_added",
                       "sign_out", "sign_in"]
    assert mine[3]["detail"] == "bike_policy.txt" and mine[0]["via"] == "web"
    everyone = client.get("/api/activity", params={"everyone": True}).json()["entries"]
    failed = [e for e in everyone if e["action"] == "sign_in_failed"]
    assert failed and failed[0]["user_name"] == "abhinav" and failed[0]["ok"] is False
    with store.engine.connect() as conn:  # document names in the log are encrypted too
        assert all(d is None or d.startswith("dv1:") for d in conn.execute(select(access_log.c.detail)).scalars())

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"name": "sunita", "password": "another long password"})
    assert client.get("/api/activity", params={"everyone": True}).status_code == 403
    assert [e["action"] for e in client.get("/api/activity").json()["entries"]] == ["sign_in"]


def test_the_admin_can_download_an_encrypted_backup(tmp_path):
    store = DocumentStore(tmp_path / "index")
    client = signed_in(create_app(store, FakeEmbedder(), TOK, frontend_dir=None))
    client.post("/api/documents", files=[("files", ("bike_policy.txt", SECRET.encode()))])
    assert client.post("/api/backup", json={"password": "short"}).status_code == 400
    r = client.post("/api/backup", json={"password": "a long backup password"})
    assert r.status_code == 200 and r.content.startswith(BACKUP_MAGIC) and b"TW-778812" not in r.content
    assert ".dvbackup" in r.headers["content-disposition"]
    assert client.get("/api/activity").json()["entries"][0]["action"] == "backup_made"


def test_vault_log_says_who_did_what_without_search_text(tmp_path, caplog):
    import logging

    caplog.set_level(logging.INFO)
    store = DocumentStore(tmp_path / "index")
    client = signed_in(create_app(store, FakeEmbedder(), TOK, frontend_dir=None), "abhinav")
    doc = client.post("/api/documents", files=[("files", ("bike_policy.txt", SECRET.encode()))]).json()[0]["document"]
    client.get(f"/api/documents/{doc['doc_id']}/file")
    client.get("/api/search", params={"q": "ABCDE1234F"})
    activity = [r.getMessage() for r in caplog.records if r.name == "vault.activity"]
    requests = [r.getMessage() for r in caplog.records if r.name == "vault.requests"]
    assert any(m.startswith("abhinav (browser) signed in") for m in activity)
    assert any(m.startswith("abhinav (browser) added a document: bike_policy.txt") for m in activity)
    assert any(m.startswith("abhinav (browser) opened a document: bike_policy.txt") for m in activity)
    assert any(m.startswith("abhinav (browser) searched: 1 documents (bike_policy.txt)") for m in activity)
    assert any(m.startswith("abhinav (browser) POST /api/documents -> 200") for m in requests)
    leaks = [(r.name, r.getMessage()) for r in caplog.records
             if "ABCDE1234F" in r.getMessage() and r.name != "httpx"]  # httpx: the test's own client
    assert not leaks, leaks  # searched text never logged
