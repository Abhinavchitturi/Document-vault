"""Phase 12: sharing documents with family members."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.retriever.retriever import Retriever
from backend.upload.ingest import ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

TOK = RegexTokenizer()
HOUSE = "Home insurance policy HI-5521 for the family house. Expires 2 January 2027. Premium Rs. 6,400."


def add(store, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return ingest_file(path, store, FakeEmbedder(), TOK).document


def test_a_shared_document_is_visible_and_searchable_but_only_the_owner_changes_it(tmp_path):
    dad = DocumentStore(tmp_path / "index", owner="dad")
    mom = dad.for_user(dad._ensure_user("mom"))
    son = dad.for_user(dad._ensure_user("son"))
    doc = add(dad, tmp_path, "house insurance.txt", HOUSE)
    add(dad, tmp_path, "dad payslip.txt", "Salary slip for September. Net pay Rs. 90,000.")
    assert mom.list_documents() == []

    dad.share(doc["doc_id"], [mom.owner_id])
    seen = mom.get(doc["doc_id"])
    assert seen and seen["owner"] == "dad" and seen["mine"] is False
    assert dad.get(doc["doc_id"])["shared_with"] == [{"id": mom.owner_id, "name": "mom"}]
    assert [d["filename"] for d in mom.list_documents()] == ["house insurance.txt"]  # not the payslip
    assert son.list_documents() == []
    hits = Retriever(mom, FakeEmbedder(), "hybrid").documents("HI-5521", top_k=3)
    assert hits and hits[0][0]["doc_id"] == doc["doc_id"]  # (chunk, score)

    with pytest.raises(PermissionError, match="Only dad"):
        mom.update_facts(doc["doc_id"], {"amount": "1"}, "person")
    with pytest.raises(PermissionError):
        mom.delete(doc["doc_id"])
    with pytest.raises(PermissionError):
        mom.share(doc["doc_id"], [son.owner_id])

    dad.update_facts(doc["doc_id"], {"amount": "6500"}, "person")
    assert mom.get(doc["doc_id"])["amount"] == "6500"  # the owner's correction reaches mom's view

    dad.share(doc["doc_id"], [son.owner_id])  # the list is replaced: son in, mom out
    assert mom.get(doc["doc_id"]) is None and son.get(doc["doc_id"]) is not None
    assert son.leave(doc["doc_id"]) and son.get(doc["doc_id"]) is None
    assert dad.get(doc["doc_id"])["shared_with"] == []

    dad.share(doc["doc_id"], [mom.owner_id, son.owner_id])
    reopened = DocumentStore(tmp_path / "index", owner="mom")  # survives a restart
    assert reopened.get(doc["doc_id"])["owner"] == "dad"
    assert dad.delete(doc["doc_id"])
    assert mom.get(doc["doc_id"]) is None and son.get(doc["doc_id"]) is None


def test_sharing_through_the_app(tmp_path):
    store = DocumentStore(tmp_path / "index")
    app = create_app(store, FakeEmbedder(), TOK, frontend_dir=None)
    dad = signed_in(app, "dad")
    assert dad.post("/api/users", json={"name": "mom", "password": "mom's long password"}).status_code == 200
    family = dad.get("/api/family").json()
    assert [p["name"] for p in family] == ["mom"]
    mom_id = family[0]["id"]
    doc = dad.post("/api/documents", files=[("files", ("house insurance.txt", HOUSE.encode()))]).json()[0]["document"]

    mom = TestClient(app)
    mom.post("/api/auth/login", json={"name": "mom", "password": "mom's long password"})
    assert mom.get("/api/documents").json() == []

    assert dad.put(f"/api/documents/{doc['doc_id']}/shares", json={"user_ids": [999]}).status_code == 400
    shared = dad.put(f"/api/documents/{doc['doc_id']}/shares", json={"user_ids": [mom_id]}).json()
    assert shared["shared_with"] == [{"id": mom_id, "name": "mom"}] and "owner_id" not in shared

    listed = mom.get("/api/documents").json()
    assert [(d["filename"], d["owner"], d["mine"]) for d in listed] == [("house insurance.txt", "dad", False)]
    assert mom.get("/api/search", params={"q": "HI-5521"}).json()["results"][0]["doc_id"] == doc["doc_id"]
    assert mom.get(f"/api/documents/{doc['doc_id']}/file").content == HOUSE.encode()
    upcoming = mom.get("/api/upcoming", params={"days": 730}).json()
    assert upcoming and upcoming[0]["owner"] == "dad" and upcoming[0]["mine"] is False

    assert mom.patch(f"/api/documents/{doc['doc_id']}", json={"category": "bill"}).status_code == 403
    assert mom.delete(f"/api/documents/{doc['doc_id']}").status_code == 403
    assert mom.put(f"/api/documents/{doc['doc_id']}/shares", json={"user_ids": []}).status_code == 403
    assert dad.delete(f"/api/documents/{doc['doc_id']}/shares/me").status_code == 404  # dad owns it

    assert mom.delete(f"/api/documents/{doc['doc_id']}/shares/me").status_code == 200
    assert mom.get("/api/documents").json() == []
    assert dad.get("/api/documents").json()[0]["shared_with"] == []
    assert mom.get("/api/search", params={"q": "HI-5521"}).json()["results"] == []

    log = [e["action"] for e in dad.get("/api/activity", params={"everyone": True}).json()["entries"]]
    assert "document_shared" in log and "share_left" in log
