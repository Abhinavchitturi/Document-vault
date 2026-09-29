import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in
from tests.pdf_helper import make_pdf


@pytest.fixture
def client(tmp_path):
    return signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer()))


def upload(client, name, content, category="auto"):
    return client.post("/api/documents", files=[("files", (name, content))], data={"category": category}).json()


def test_page_and_empty_library(client):
    assert "Document Vault" in client.get("/").text
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


def test_ask_returns_answer_with_cited_sources(tmp_path):
    from tests.fakes import FakeLLM

    llm = FakeLLM("Your bike policy expires on 14 March 2027 [1].")
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), llm))
    upload(client, "bike_policy.pdf", make_pdf(["Two-wheeler policy expires on 14 March 2027"]))
    upload(client, "bill.txt", b"Electricity bill due 18 September")
    data = client.post("/api/ask", json={"question": "when does the policy expire", "k": 2}).json()
    assert data["answer"] == "Your bike policy expires on 14 March 2027 [1]."
    assert data["citations"] == [1] and data["found"] and data["llm"] == "fake/llm"
    assert data["sources"][0]["filename"] == "bike_policy.pdf" and len(data["sources"]) == 2
    assert "[1] bike_policy.pdf (insurance), page 1" in llm.last_prompt
    info = client.get("/api/info").json()
    assert info["llm"] == "fake/llm" and info["llm_status"]["ready"]


def test_ask_when_llm_is_off_or_down(tmp_path):
    from backend.llm.ollama import LLMUnavailableError
    from tests.fakes import FakeLLM

    no_llm = signed_in(create_app(DocumentStore(tmp_path / "a"), FakeEmbedder(), RegexTokenizer()))
    assert no_llm.post("/api/ask", json={"question": "x"}).status_code == 503
    assert no_llm.get("/api/info").json()["llm_status"]["ready"] is False
    down = FakeLLM(error=LLMUnavailableError("Can't reach Ollama on this computer."))
    client = signed_in(create_app(DocumentStore(tmp_path / "b"), FakeEmbedder(), RegexTokenizer(), down))
    upload(client, "a.txt", b"Water bill due 15 July")
    res = client.post("/api/ask", json={"question": "water bill"})
    assert res.status_code == 503 and "Ollama" in res.json()["detail"]


def _chat(client, **body):
    res = client.post("/api/chat", json=body)
    return res, [json.loads(line) for line in res.text.splitlines() if line.strip()]


def test_chat_streams_sources_then_answer_then_citations(tmp_path):
    from tests.fakes import FakeLLM

    llm = FakeLLM("Your August electricity bill is Rs. 2,486 [1].")
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), llm))
    upload(client, "electricity_aug.pdf", make_pdf(["Electricity bill August. Amount payable Rs. 2,486"]))
    upload(client, "fridge.txt", b"Refrigerator warranty, compressor 10 years")
    res, events = _chat(client, question="electricity bill august")
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-ndjson")
    assert events[0]["type"] == "sources" and events[0]["sources"][0]["filename"] == "electricity_aug.pdf"
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert len(tokens) > 1 and "".join(tokens) == "Your August electricity bill is Rs. 2,486 [1]."
    assert events[-1] == {"type": "done", "citations": [1], "found": True, "llm": "fake/llm"}


def test_chat_follow_up_uses_history(tmp_path):
    from tests.fakes import FakeLLM

    llm = FakeLLM("It is due on 18 September 2026 [1].")
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), llm))
    upload(client, "electricity_aug.pdf", make_pdf(["Electricity bill August. Due date 18 September 2026"]))
    history = [{"role": "user", "content": "electricity bill august"},
               {"role": "assistant", "content": "It is Rs. 2,486 [1]."}]
    _, events = _chat(client, question="and when is it due?", history=history)
    assert any(s["filename"] == "electricity_aug.pdf" for s in events[0]["sources"])  # found via the earlier question
    assert llm.last_history == [{"role": "user", "content": "electricity bill august"},
                                {"role": "assistant", "content": "It is Rs. 2,486."}]  # old [1] dropped


def test_chat_reports_llm_problems_as_an_event(tmp_path):
    from backend.llm.ollama import LLMUnavailableError
    from tests.fakes import FakeLLM

    down = FakeLLM(error=LLMUnavailableError("Can't reach Ollama on this computer."))
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), down))
    upload(client, "a.txt", b"Water bill due 15 July")
    _, events = _chat(client, question="water bill")
    assert [e["type"] for e in events] == ["sources", "error"] and "Ollama" in events[1]["message"]
    no_llm = signed_in(create_app(DocumentStore(tmp_path / "b"), FakeEmbedder(), RegexTokenizer()))
    assert no_llm.post("/api/chat", json={"question": "x"}).status_code == 503
