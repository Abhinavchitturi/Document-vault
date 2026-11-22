"""The answer model through llama.cpp (backend/llm/llamacpp.py) and its one-time download (backend/llm/setup.py),
against a pretend llama-server and a pretend download host."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.llm import llamacpp, setup
from backend.llm.llamacpp import LlamaCppLLM
from backend.llm.llamacpp import LLMUnavailableError


class Pretend(BaseHTTPRequestHandler):
    """llama-server's API, and a file host that honours Range requests."""

    health = 200
    requests: list[dict] = []
    files: dict[str, bytes] = {}

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(Pretend.health)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        body = Pretend.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Pretend.requests.append(body)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("Your bike ", "insurance expires ", "on 14 March 2027 [1]."):
                self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n".encode())
            self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return
        content = '{"category": "bill", "amount": 2486}' if "response_format" in body else "Paris."
        reply = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


@pytest.fixture
def pretend():
    Pretend.health, Pretend.requests, Pretend.files = 200, [], {}
    server = HTTPServer(("127.0.0.1", 0), Pretend)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_only_this_computer_is_ever_asked():
    with pytest.raises(ValueError):
        LlamaCppLLM(host="http://192.168.1.20:8091")
    with pytest.raises(ValueError):
        LlamaCppLLM(host="https://api.example.com")


def test_answers_streaming_and_json(pretend):
    llm = LlamaCppLLM(host=pretend)
    assert llm.generate("Be brief.", "Capital of France?") == "Paris."
    assert Pretend.requests[-1]["temperature"] == 0 and Pretend.requests[-1]["messages"][0]["role"] == "system"
    assert "".join(llm.stream("Be brief.", "When does it expire?")) == "Your bike insurance expires on 14 March 2027 [1]."
    schema = {"type": "object", "properties": {"category": {"type": "string"}}}
    assert llm.generate_json("Classify.", "Electricity bill", schema) == {"category": "bill", "amount": 2486}
    assert Pretend.requests[-1]["response_format"]["json_schema"]["schema"] == schema
    assert llm.name == "llama.cpp/llama3.1:8b" and llm.list_models() == ["llama3.1:8b"]


def test_status_while_loading_and_when_not_running(pretend):
    llm = LlamaCppLLM(host=pretend)
    assert llm.status()[0]
    Pretend.health = 503
    ready, message = llm.status()
    assert not ready and "loading" in message
    down = LlamaCppLLM(host="http://127.0.0.1:9")
    assert not down.status()[0]
    with pytest.raises(LLMUnavailableError, match="Restart the vault"):
        down.generate("s", "p")


def _with_files(tmp_path, monkeypatch, pretend, server_bytes=b"#!/bin/sh\necho llama\n", model_bytes=b"GGUF" * 1000):
    """Point the setup at the pretend host, with a build archive and a model of known checksums."""
    monkeypatch.setenv("VAULT_LLM_DIR", str(tmp_path / "llm"))
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    packed = io.BytesIO()
    if sys.platform == "win32":
        archive = "llama-test-bin.zip"
        with zipfile.ZipFile(packed, "w") as z:
            z.writestr(f"llama-test/{exe}", server_bytes)
    else:
        archive = "llama-test-bin.tar.gz"
        with tarfile.open(fileobj=packed, mode="w:gz") as t:
            info = tarfile.TarInfo(f"llama-test/{exe}")
            info.size, info.mode = len(server_bytes), 0o755
            t.addfile(info, io.BytesIO(server_bytes))
    Pretend.files = {f"/release/{archive}": packed.getvalue(), "/model.gguf": model_bytes}
    monkeypatch.setattr(llamacpp, "RELEASES", f"{pretend}/release")
    monkeypatch.setattr(llamacpp, "binary_for_this_computer",
                        lambda: (archive, hashlib.sha256(packed.getvalue()).hexdigest()))
    monkeypatch.setattr(llamacpp, "MODEL", {"label": "test", "file": "model.gguf", "url": f"{pretend}/model.gguf",
                                            "size": len(model_bytes), "sha256": hashlib.sha256(model_bytes).hexdigest()})
    return model_bytes


def quiet(*args, **kwargs):
    pass


def test_setup_downloads_checks_and_unpacks_everything(tmp_path, monkeypatch, pretend):
    _with_files(tmp_path, monkeypatch, pretend)
    assert not llamacpp.installed()
    server = setup.install_server(say=quiet)
    model = setup.install_model(say=quiet)
    assert server == llamacpp.server_path() and model == llamacpp.model_path() and llamacpp.installed()
    assert not list((tmp_path / "llm").rglob("*.partial")) and not list((tmp_path / "llm" / "downloads").glob("*"))
    assert setup.install_model(say=quiet) == model  # already there: nothing downloaded again


def test_an_interrupted_model_download_continues_where_it_stopped(tmp_path, monkeypatch, pretend):
    model_bytes = _with_files(tmp_path, monkeypatch, pretend)
    partial = tmp_path / "llm" / "models" / "model.gguf.unchecked.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(model_bytes[:1500])
    setup.install_model(say=quiet)
    assert (tmp_path / "llm" / "models" / "model.gguf").read_bytes() == model_bytes


def test_a_download_that_does_not_match_its_checksum_is_thrown_away(tmp_path, monkeypatch, pretend):
    _with_files(tmp_path, monkeypatch, pretend)
    Pretend.files["/model.gguf"] = b"X" * len(Pretend.files["/model.gguf"])  # same size, wrong bytes
    with pytest.raises(setup.SetupError, match="checksum"):
        setup.install_model(say=quiet)
    assert llamacpp.model_path() is None and not list((tmp_path / "llm" / "models").glob("*"))

    monkeypatch.setattr(llamacpp, "binary_for_this_computer", lambda: ("llama-test-bin" + (".zip" if sys.platform == "win32" else ".tar.gz"), "0" * 64))
    with pytest.raises(setup.SetupError, match="checksum"):
        setup.install_server(say=quiet)
    assert llamacpp.server_path() is None


def test_the_vault_reports_the_local_ai_when_it_is_downloaded(tmp_path, monkeypatch, pretend):
    from tests.test_deploy import FakeMac, vault

    _with_files(tmp_path, monkeypatch, pretend)
    setup.install_server(say=quiet)
    setup.install_model(say=quiet)
    mac = FakeMac()
    v, _ = vault(tmp_path, mac)
    answers = [c for c in v.checks(fix=True) if c.name.startswith("Answers")][0]
    assert answers.ok and "llama.cpp" in answers.message


def test_every_supported_computer_has_a_pinned_build():
    for (system, machine), (name, digest) in llamacpp.BINARIES.items():
        assert llamacpp.BUILD in name and len(digest) == 64
    assert len(llamacpp.MODEL["sha256"]) == 64 and llamacpp.MODEL["url"].startswith("https://huggingface.co/")


def test_one_click_download_from_the_app(tmp_path, monkeypatch, pretend):
    import time

    from backend.api.app import create_app
    from backend.chunker.chunker import RegexTokenizer
    from backend.llm.local_ai import LocalAI
    from backend.vector_store.store import DocumentStore
    from tests.fakes import FakeEmbedder, signed_in

    _with_files(tmp_path, monkeypatch, pretend)

    class Server:  # stands in for llama-server: records that the vault started it
        server = model = None
        started = False

        def start(self):
            self.started = True

    class NotYet:  # the answer model, before it is downloaded
        name = "llama.cpp/test"

        def status(self):
            return (llamacpp.installed(), "The local AI isn't downloaded yet.")

    server = Server()
    app = create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), NotYet(),
                     frontend_dir=None, local_ai=LocalAI(server))
    admin = signed_in(app, "abhinav")
    status = admin.get("/api/info").json()["llm_status"]
    assert not status["ready"] and status["can_download"] and "5 GB" in status["message"]
    assert admin.get("/api/ai/download").json()["state"] == "idle"

    assert admin.post("/api/ai/download").json()["state"] == "downloading"
    deadline = time.monotonic() + 20
    while admin.get("/api/ai/download").json()["state"] == "downloading" and time.monotonic() < deadline:
        time.sleep(0.1)
    done = admin.get("/api/ai/download").json()
    assert done["state"] == "done" and done["error"] is None
    assert llamacpp.installed() and server.started and server.model == llamacpp.model_path()  # started, no restart
    after = admin.get("/api/info").json()["llm_status"]
    assert after["ready"] and not after["can_download"]
    assert admin.post("/api/ai/download").status_code == 409  # nothing left to download

    admin.post("/api/users", json={"name": "sunita", "password": "another long password"})
    admin.post("/api/auth/logout")
    admin.post("/api/auth/login", json={"name": "sunita", "password": "another long password"})
    assert admin.post("/api/ai/download").status_code == 403  # only an admin downloads it
