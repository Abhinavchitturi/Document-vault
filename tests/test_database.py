"""Phase 2: document metadata in the database (runs on SQLite and on PostgreSQL, see conftest.py)."""

import json

import numpy as np
import pytest
from sqlalchemy import inspect, select

from backend.chunker.chunker import RegexTokenizer
from backend.database.db import DatabaseUnavailableError, connect, default_owner
from backend.database.schema import document_chunks, documents, users
from backend.upload.ingest import ingest_file
from backend.vector_store import store as store_module
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder

TOK = RegexTokenizer()


def write(path, text):
    path.write_text(text)
    return path


def test_schema_has_the_planned_tables_and_columns(tmp_path):
    store = DocumentStore(tmp_path / "index")
    tables = inspect(store.engine)
    assert {"users", "documents", "document_chunks"} <= set(tables.get_table_names())
    columns = {c["name"] for c in tables.get_columns("documents")}
    assert {"name", "uploaded_at", "doc_type", "owner_id", "file_path"} <= columns  # the plan's fields


def test_upload_writes_rows_with_owner_type_date_and_path(tmp_path):
    store, emb = DocumentStore(tmp_path / "index", owner="arjun"), FakeEmbedder()
    ingest_file(write(tmp_path / "bike_policy.txt", "Two-wheeler insurance policy. Expires 14 March 2027."),
                store, emb, TOK)
    with store.engine.connect() as conn:
        row = conn.execute(
            select(documents, users.c.name.label("owner")).join(users, users.c.id == documents.c.owner_id)
        ).mappings().one()
        chunk_texts = conn.execute(select(document_chunks.c.text)).scalars().all()
    assert (row["owner"], row["name"], row["doc_type"]) == ("arjun", "bike_policy.txt", "insurance")
    assert row["uploaded_at"] is not None and (store.files_dir / row["file_path"]).exists()
    assert chunk_texts and "14 March 2027" in chunk_texts[0]
    assert store.counts_by_category() == {"insurance": 1}


def test_each_owner_sees_only_their_documents(tmp_path):
    (tmp_path / "shared").mkdir()
    url = store_module._default_url(tmp_path / "shared")
    root, emb = tmp_path / "index", FakeEmbedder()
    bill = write(tmp_path / "bill.txt", "Electricity bill due 18 September")
    policy = write(tmp_path / "policy.txt", "Car insurance policy expires 1 August")

    arjun = DocumentStore(root, url, owner="arjun")
    ingest_file(bill, arjun, emb, TOK)
    kavya = DocumentStore(root, url, owner="kavya")
    ingest_file(policy, kavya, emb, TOK)
    ingest_file(bill, kavya, emb, TOK)  # the same file may belong to two people

    arjun = DocumentStore(root, url, owner="arjun")  # reload
    assert [d["filename"] for d in arjun.list_documents()] == ["bill.txt"]
    hits = arjun.search(emb.encode_queries(["car insurance policy expires"])[0], top_k=5)
    assert [c["filename"] for c, _ in hits] == ["bill.txt"]  # kavya's policy is invisible to arjun
    assert sorted(d["filename"] for d in DocumentStore(root, url, owner="kavya").list_documents()) == ["bill.txt", "policy.txt"]


def test_delete_removes_rows_vectors_and_file(tmp_path):
    store, emb = DocumentStore(tmp_path / "index"), FakeEmbedder()
    doc = ingest_file(write(tmp_path / "tv.txt", "Television warranty until 2027"), store, emb, TOK).document
    stored = store.file_path(doc["doc_id"])
    assert store.delete(doc["doc_id"])
    with store.engine.connect() as conn:
        assert conn.execute(select(documents)).first() is None
        assert conn.execute(select(document_chunks)).first() is None
    assert not stored.exists() and store.num_vectors == 0


def test_json_vault_from_before_phase_2_is_migrated(tmp_path):
    root, emb = tmp_path / "index", FakeEmbedder()
    (root / "files").mkdir(parents=True)
    doc = {"doc_id": "abc123", "filename": "fridge.txt", "category": "warranty", "pages": 1, "ocr_pages": 0,
           "num_chunks": 1, "size_bytes": 40, "sha256": "f" * 64, "stored_as": "abc123.txt",
           "added_at": "2026-09-01T10:00:00+00:00"}
    chunk = {"vid": 0, "chunk_id": "abc123-p1-0", "doc_id": "abc123", "filename": "fridge.txt",
             "category": "warranty", "page": 1, "text": "Refrigerator compressor warranty 10 years", "n_tokens": 5}
    (root / "documents.json").write_text(json.dumps([doc]))
    (root / "chunks.jsonl").write_text(json.dumps(chunk) + "\n")
    (root / "meta.json").write_text(json.dumps({"model": emb.model_name}))
    (root / "files" / "abc123.txt").write_text("Refrigerator compressor warranty 10 years")
    import faiss

    index = faiss.IndexIDMap2(faiss.IndexFlatIP(256))
    index.add_with_ids(emb.encode_passages([chunk["text"]]), np.array([0], dtype=np.int64))
    faiss.write_index(index, str(root / "vectors.faiss"))

    store = DocumentStore(root, owner="arjun")
    assert [d["filename"] for d in store.list_documents()] == ["fridge.txt"]
    assert store.model == emb.model_name and not store.missing_vectors
    assert store.search(emb.encode_queries(["compressor warranty"])[0])[0][0]["filename"] == "fridge.txt"
    assert (root / "documents.json.migrated").exists() and not (root / "documents.json").exists()
    assert len(DocumentStore(root, owner="arjun").documents) == 1  # migrated once, not twice


def test_missing_vectors_are_detected_and_rebuilt(tmp_path):
    root, emb = tmp_path / "index", FakeEmbedder()
    store = DocumentStore(root)
    ingest_file(write(tmp_path / "a.txt", "Water bill due 15 July"), store, emb, TOK)
    (root / "vectors.faiss").unlink()  # simulate a crash that lost the vector file
    store = DocumentStore(root)
    assert store.missing_vectors
    store.reembed(lambda chunks: emb.encode_passages([c["text"] for c in chunks]), emb.model_name)
    assert not DocumentStore(root).missing_vectors


def test_database_must_be_local_and_reachable(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="this computer"):
        connect("postgresql+psycopg://db.example.com/vault")
    with pytest.raises(ValueError, match="Unsupported"):
        connect("mysql://localhost/vault")
    with pytest.raises(DatabaseUnavailableError, match="brew services start postgresql"):
        connect("postgresql+psycopg://nobody@localhost:9/vault")  # nothing listens on port 9
    monkeypatch.setenv("VAULT_OWNER", "kavya")
    assert default_owner() == "kavya" and default_owner("arjun") == "arjun"



class _PgError(Exception):
    pass


@pytest.mark.parametrize("server_says,expected", [
    ('connection failed: FATAL:  role "Abhinav" does not exist',
     ['PostgreSQL says: role "Abhinav" does not exist', 'CREATE ROLE "Abhinav" WITH LOGIN SUPERUSER', "OLDNAME@localhost/vault"]),
    ('connection failed: FATAL:  database "vault" does not exist', ["createdb vault"]),
    ("connection failed: Connection refused\n\tIs the server running?", ["brew services start postgresql@16"]),
    ('FATAL:  password authentication failed for user "x"', ["user name or password"]),
])
def test_connection_errors_say_what_is_wrong_and_how_to_fix_it(server_says, expected):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    from backend.database.db import explain_connection_error

    message = explain_connection_error(make_url("postgresql+psycopg://localhost/vault"),
                                       OperationalError("connect", {}, _PgError(server_says)))
    for text in expected:
        assert text in message, (text, message)
