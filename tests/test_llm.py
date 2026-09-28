import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.embeddings.embedder import prefer_offline
from backend.llm.answer import NOT_FOUND, answer_question, build_prompt
from backend.llm.ollama import LLMUnavailableError, OllamaLLM
from tests.fakes import FakeLLM

PASSAGES = [
    {"filename": "Bike Insurance 2025.pdf", "category": "insurance", "page": 1, "text": "Period: 15 March 2025 to 14 March 2026."},
    {"filename": "Bike Insurance 2026.pdf", "category": "insurance", "page": 1, "text": "Period: 15 March 2026 to 14 March 2027."},
]


def test_prompt_numbers_passages_with_their_source():
    prompt = build_prompt("When does it expire?", PASSAGES)
    assert "[1] Bike Insurance 2025.pdf (insurance), page 1:\nPeriod: 15 March 2025" in prompt
    assert "[2] Bike Insurance 2026.pdf" in prompt and prompt.endswith("Question: When does it expire?")


def test_answer_parses_citations_and_passes_today():
    llm = FakeLLM("Your bike insurance expires on 14 March 2027 [2]. The older policy ended in 2026 [1][7].")
    answer = answer_question("When does my bike insurance expire?", PASSAGES, llm, today=date(2026, 9, 28))
    assert answer.citations == [1, 2]  # [7] doesn't exist and is ignored
    assert answer.found
    assert "28 September 2026" in llm.last_system


def test_not_found_is_reported_and_no_passages_skips_the_llm():
    assert not answer_question("Q?", PASSAGES, FakeLLM(NOT_FOUND)).found
    llm = FakeLLM("should not be called")
    answer = answer_question("Q?", [], llm)
    assert not answer.found and llm.calls == 0


def test_ollama_only_talks_to_this_computer():
    for host in ("http://192.168.1.20:11434", "https://api.example.com"):
        with pytest.raises(ValueError, match="this computer"):
            OllamaLLM(host=host)
    OllamaLLM(host="http://localhost:11434")


class _FakeOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if body["model"] == "missing":
            self.send_response(404); self.end_headers()
            self.wfile.write(b'{"error":"model \\"missing\\" not found, try pulling it first"}')
            return
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"message": {"role": "assistant", "content": " It expires on 14 March 2027 [2]. "}}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_ollama():
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_ollama_request_and_errors(fake_ollama):
    host = f"http://127.0.0.1:{fake_ollama.server_port}"
    assert OllamaLLM("llama3.1:8b", host=host).generate("sys", "prompt") == "It expires on 14 March 2027 [2]."
    sent = fake_ollama.requests[0]
    assert sent["stream"] is False and sent["options"]["temperature"] == 0
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    with pytest.raises(LLMUnavailableError, match="ollama pull missing"):
        OllamaLLM("missing", host=host).generate("sys", "prompt")


def test_ollama_not_running_explains_how_to_start_it():
    with pytest.raises(LLMUnavailableError, match="Ollama"):
        OllamaLLM(host="http://127.0.0.1:9").generate("sys", "prompt")  # nothing listens on port 9


def test_prefer_offline_only_when_model_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert not prefer_offline("BAAI/bge-small-en-v1.5")
    (tmp_path / "models--BAAI--bge-small-en-v1.5" / "snapshots" / "abc123").mkdir(parents=True)
    assert prefer_offline("BAAI/bge-small-en-v1.5")
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_reasoning_models_thinking_is_removed():
    llm = FakeLLM("<think>The 2026 policy is current, so...</think>\nIt expires on 14 March 2027 [2].")
    assert answer_question("Q?", PASSAGES, llm).text == "It expires on 14 March 2027 [2]."


def test_compare_ranks_models_and_skips_missing_ones():
    from backend.eval.compare_llms import compare

    class Named(FakeLLM):
        def __init__(self, name, reply=None, error=None):
            super().__init__(reply, error)
            self.name = name

    questions = [{"question": "When does it expire?", "answer": "14 March 2027"},
                 {"question": "Policy number?", "answer": "SGI/TW/2026/448812"}]
    contexts = [PASSAGES, PASSAGES]
    rows = compare(questions, contexts, [
        Named("weak", "I couldn't find that in your documents."),
        Named("strong", "It expires on 14 March 2027 [2], policy SGI/TW/2026/448812 [2]."),
        Named("missing", error=LLMUnavailableError("The model missing isn't downloaded yet. Run: ollama pull missing")),
    ])
    assert [r["model"] for r in rows] == ["strong", "weak", "missing"]
    assert rows[0]["accuracy"] == 1.0 and rows[0]["cited_a_source"] == 2
    assert rows[1]["accuracy"] == 0.0 and not rows[2]["available"] and "ollama pull" in rows[2]["error"]
