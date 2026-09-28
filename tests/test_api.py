import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.ingestion.chunker import RegexTokenizer
from src.retrieval.document_store import DocumentStore
from tests.fakes import FakeEmbedder
from tests.pdf_helper import make_pdf


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer()))


def upload(client, name, content, category="auto"):
    return client.post("/api/documents", files=[("files", (name, content))], data={"category": category}).json()


def test_page_and_empty_library(client):
    assert "Family Documents" in client.get("/").text
    info = client.get("/api/info").json()
    assert info["num_documents"] == 0 and set(info["categories"]) == {"insurance", "warranty", "bill", "property", "other"}
    assert client.get("/api/search", params={"q": "anything"}).json()["results"] == []


def test_upload_search_open_delete(client):
    [res] = upload(client, "bike_policy.pdf", make_pdf(["Two-wheeler policy", "Policy expires on 14 March 2027"]))
    assert res["status"] == "added"
    doc = res["document"]
    assert doc["category"] == "insurance" and doc["pages"] == 2 and "sha256" not in doc

    upload(client, "fridge.md", b"Refrigerator warranty. Compressor covered for 10 years.", category="warranty")
    assert len(client.get("/api/documents").json()) == 2

    # The fake embedder matches exact words only, hence "expires".
    hit = client.get("/api/search", params={"q": "when the policy expires"}).json()["results"][0]
    assert (hit["filename"], hit["page"], hit["category"]) == ("bike_policy.pdf", 2, "insurance")
    only_warranty = client.get("/api/search", params={"q": "policy expire", "category": "warranty"}).json()
    assert [r["filename"] for r in only_warranty["results"]] == ["fridge.md"]

    file_res = client.get(f"/api/documents/{doc['doc_id']}/file")
    assert file_res.status_code == 200 and file_res.content.startswith(b"%PDF")
    assert client.delete(f"/api/documents/{doc['doc_id']}").json() == {"deleted": doc["doc_id"]}
    assert client.get(f"/api/documents/{doc['doc_id']}/file").status_code == 404
    assert client.get("/api/info").json()["num_documents"] == 1


def test_duplicate_bad_type_and_bad_category(client):
    upload(client, "a.txt", b"Water bill due 15 July")
    assert upload(client, "copy_of_a.txt", b"Water bill due 15 July")[0]["status"] == "duplicate"
    bad = upload(client, "sheet.xlsx", b"x")[0]
    assert bad["status"] == "error" and "Unsupported" in bad["error"]
    assert upload(client, "empty.txt", b"   ")[0]["status"] == "error"
    res = client.post("/api/documents", files=[("files", ("x.txt", b"hi"))], data={"category": "cars"})
    assert res.status_code == 400


def test_uploaded_filename_cannot_escape_the_library(client, tmp_path):
    [res] = upload(client, "../../evil.txt", b"Warranty card for the mixer grinder")
    assert res["document"]["filename"] == "evil.txt"
    assert not (tmp_path / "evil.txt").exists()
