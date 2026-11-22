"""1.0: several computers keep one vault (backend/sync)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.sync import records, stamps


def test_the_newest_change_wins_and_both_computers_agree_on_ties():
    ours = {"changed_at": 100.0, "changed_by": "centre-a"}
    assert stamps.newer({"changed_at": 101.0, "changed_by": "centre-a"}, ours)
    assert not stamps.newer({"changed_at": 99.0, "changed_by": "centre-z"}, ours)
    assert stamps.newer({"changed_at": 100.0, "changed_by": "centre-b"}, ours)  # same moment: the higher id, everywhere
    assert not stamps.newer({"changed_at": 100.0, "changed_by": "centre-a"}, ours)
    assert stamps.newer({"changed_at": 1.0, "changed_by": "x"}, None)


def test_a_document_travels_whole_to_another_vault(tmp_path, monkeypatch):
    """Copied with its file (still sealed), search passages, vectors, owner and facts; then deleted for good."""
    from backend.chunker.chunker import RegexTokenizer
    from backend.security import keys
    from backend.upload import ingest
    from backend.vector_store.store import DocumentStore
    from tests.fakes import FakeEmbedder

    monkeypatch.setenv("VAULT_KEYCHAIN", "off")
    monkeypatch.setenv("VAULT_KEY_DIR", str(tmp_path / "keys"))
    a = DocumentStore(tmp_path / "a" / "index", owner="abhinav")
    doc = tmp_path / "Bike Insurance 2026.txt"
    doc.write_text("Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.")
    added = ingest.ingest_file(doc, a, FakeEmbedder(), RegexTokenizer(), "insurance").document

    b = DocumentStore(tmp_path / "b" / "index", owner="someone")
    b.adopt_vault(a._vault.vault_id, keys.load(a._vault.vault_id))  # what joining does
    with a.engine.connect() as conn:
        stamp = stamps.get(conn, "doc", added["doc_id"])
    assert stamp is not None  # adding stamped it
    record = records.export_doc(a, added["doc_id"])
    records.apply_doc(b, record, records.doc_files(a, record["stored_as"]), stamp)

    mine = b.for_user(b._ensure_user("abhinav"))
    copied = mine.get(added["doc_id"])
    assert copied["filename"] == "Bike Insurance 2026.txt" and copied["owner"] == "abhinav"
    assert mine.read_file(added["doc_id"]) == doc.read_bytes()  # the sealed file opens with the shared key
    assert len(mine.chunks_of(added["doc_id"])) == added["num_chunks"] and b.num_vectors == a.num_vectors
    with b.engine.connect() as conn:
        assert stamps.get(conn, "doc", added["doc_id"])["changed_by"] == stamp["changed_by"]  # not sent back as new

    a.delete(added["doc_id"])
    with a.engine.connect() as conn:
        gone = stamps.get(conn, "doc", added["doc_id"])
    assert gone["gone"] and stamps.newer(gone, stamp)
    records.remove_doc(b, added["doc_id"], gone)
    assert mine.get(added["doc_id"]) is None and not (b.files_dir / record["stored_as"]).exists()


def test_people_and_devices_are_stamped(tmp_path):
    from backend.auth.accounts import Accounts
    from backend.auth.devices import Devices
    from backend.vector_store.store import DocumentStore

    store = DocumentStore(tmp_path / "index")
    accounts, devices = Accounts(store.engine), Devices(store.engine)
    admin = accounts.setup("abhinav", "a long enough password 1")
    code, _ = devices.start_pairing(admin["id"], typed=True)
    _, device, token = devices.pair(code, "Laptop")
    with store.engine.connect() as conn:
        assert stamps.get(conn, "user", "abhinav") is not None
        device_stamp = [s for s in stamps.manifest(conn) if s["kind"] == "device"][0]
    assert not device_stamp["gone"]
    devices.revoke(admin["id"], device["id"])
    with store.engine.connect() as conn:
        assert stamps.get(conn, "device", device_stamp["key"])["gone"]  # revoked on the other computers too


@pytest.mark.skipif(os.environ.get("VAULT_SYNC_E2E") != "1", reason="two real engines: set VAULT_SYNC_E2E=1")
def test_two_real_vaults_kept_as_one():
    script = Path(__file__).with_name("sync_two_vaults.py")
    run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=900)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
