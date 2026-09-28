"""Phase 3: serving the web app, settings, re-filing documents, security headers."""

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, FakeLLM
from tests.pdf_helper import make_pdf


class ModelLLM(FakeLLM):
    """Like the Ollama client: has a model name and a list of downloaded models."""

    def __init__(self):
        super().__init__()
        self.model = "llama3.1:8b"
        self.name = "ollama/llama3.1:8b"

    def list_models(self):
        return ["gemma3:12b", "llama3.1:8b"]


@pytest.fixture
def web(tmp_path):
    site = tmp_path / "out"
    (site / "chat").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html><title>Document Vault</title>dashboard")
    (site / "chat" / "index.html").write_text("<!doctype html>chat page")
    llm = ModelLLM()
    app = create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), llm,
                     frontend_dir=site, database_label="postgresql+psycopg://localhost/vault")
    return TestClient(app), llm


def upload(client, name, content, category="auto"):
    return client.post("/api/documents", files=[("files", (name, content))], data={"category": category}).json()[0]


def test_built_pages_are_served_next_to_the_api(web):
    client, _ = web
    home = client.get("/")
    assert home.status_code == 200 and "dashboard" in home.text
    assert "default-src 'self'" in home.headers["content-security-policy"]
    assert home.headers["x-frame-options"] == "DENY"
    assert client.get("/chat/").text == "<!doctype html>chat page"
    assert client.get("/api/info").json()["num_documents"] == 0  # API still wins over the static mount
    assert client.get("/docs").status_code == 404  # no auto-generated API docs page


def test_unbuilt_frontend_explains_how_to_build(tmp_path):
    client = TestClient(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), frontend_dir=None))
    page = client.get("/")
    assert "npm run build" in page.text and "content-security-policy" in page.headers


def test_pdf_files_are_not_given_the_page_policy(web):
    client, _ = web
    doc = upload(client, "policy.pdf", make_pdf(["Policy expires 14 March 2027"]))["document"]
    res = client.get(f"/api/documents/{doc['doc_id']}/file")
    assert res.status_code == 200 and "content-security-policy" not in res.headers
    assert res.headers["x-content-type-options"] == "nosniff"


def test_documents_can_be_refiled(web):
    client, _ = web
    doc = upload(client, "scan001.txt", b"Refrigerator compressor covered for 10 years")["document"]
    res = client.patch(f"/api/documents/{doc['doc_id']}", json={"category": "warranty"})
    assert res.status_code == 200 and res.json()["category"] == "warranty"
    hits = client.get("/api/search", params={"q": "compressor", "category": "warranty"}).json()["results"]
    assert [h["filename"] for h in hits] == ["scan001.txt"]
    assert client.get("/api/info").json()["categories"]["warranty"] == 1
    assert client.patch(f"/api/documents/{doc['doc_id']}", json={"category": "cars"}).status_code == 400
    assert client.patch("/api/documents/nope", json={"category": "bill"}).status_code == 404


def test_settings_show_setup_and_switch_the_answer_model(web):
    client, llm = web
    s = client.get("/api/settings").json()
    assert s["llm_model"] == "llama3.1:8b" and s["llm_models"] == ["gemma3:12b", "llama3.1:8b"]
    assert s["database"] == "postgresql+psycopg://localhost/vault" and s["owner"] == "family"
    assert client.post("/api/settings", json={"llm_model": "gemma3:12b"}).json()["llm_model"] == "gemma3:12b"
    assert llm.model == "gemma3:12b"
    bad = client.post("/api/settings", json={"llm_model": "phi4"})
    assert bad.status_code == 400 and "ollama pull phi4" in bad.json()["detail"]
