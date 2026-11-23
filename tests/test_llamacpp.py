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
    label = llamacpp.MODEL["label"] if llamacpp.MODEL else "none"  # by this computer's memory
    assert llm.name == f"llama.cpp/{label}" and llm.list_models() == [label]


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
                                            "size": len(model_bytes), "sha256": hashlib.sha256(model_bytes).hexdigest(),
                                            "context": 2048})
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
    for model in llamacpp.MODELS.values():
        assert len(model["sha256"]) == 64 and model["url"].startswith("https://huggingface.co/") and model["context"]
        assert model["file"] in model["url"]


def test_the_model_fits_the_computers_memory(monkeypatch):
    monkeypatch.delenv("VAULT_AI_MODEL", raising=False)
    gb = 2**30
    assert llamacpp.choose_model(32 * gb)["label"] == "llama3.1:8b"
    assert llamacpp.choose_model(int(15.7 * gb))["label"] == "llama3.1:8b"  # a "16 GB" laptop
    assert llamacpp.choose_model(int(7.8 * gb))["label"] == "llama3.2:3b"  # an "8 GB" laptop: the small one
    assert llamacpp.choose_model(int(5.8 * gb))["label"] == "llama3.2:3b"  # a "6 GB" laptop too
    assert llamacpp.choose_model(4 * gb) is None  # too little for any
    monkeypatch.setattr(llamacpp, "total_memory", lambda: None)
    assert llamacpp.choose_model()["label"] == "llama3.1:8b"  # can't tell: as before
    monkeypatch.setenv("VAULT_AI_MODEL", "small")
    assert llamacpp.choose_model(64 * gb)["label"] == "llama3.2:3b"
    monkeypatch.undo()
    assert llamacpp.total_memory() > 1 * gb  # this computer's, read for real


def test_too_little_memory_says_so_and_offers_no_download(monkeypatch, tmp_path):
    from backend.llm.local_ai import LocalAI

    monkeypatch.setattr(llamacpp, "MODEL", None)
    monkeypatch.setattr(llamacpp, "total_memory", lambda: 4 * 2**30)
    server = llamacpp.LlamaServer(log=tmp_path / "log")
    assert server.model is None and not LocalAI(server).downloadable()
    ready, message = llamacpp.LlamaCppLLM(server=server).status()
    assert not ready and "4.0 GB of memory" in message and "Searching still finds" in message


def test_a_new_model_replaces_the_one_used_before(tmp_path, monkeypatch, pretend):
    model_bytes = _with_files(tmp_path, monkeypatch, pretend)
    models = llamacpp.llm_dir() / "models"
    models.mkdir(parents=True, exist_ok=True)
    old = models / llamacpp.MODELS["large"]["file"]
    old.write_bytes(b"the 8B model, too big for this computer")
    setup.install_model(say=lambda *a, **k: None)
    assert (models / "model.gguf").read_bytes() == model_bytes and not old.exists()


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
    assert not status["ready"] and status["can_download"] and "GB, once" in status["message"]
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


def test_the_apps_engine_downloads_the_local_ai_by_itself_after_the_search_model(tmp_path, monkeypatch, pretend):
    """A computer that keeps the vault can't answer without the local AI: it downloads by itself, search model first."""
    import time

    from backend.llm.local_ai import LocalAI

    _with_files(tmp_path, monkeypatch, pretend)

    class Server:
        server = model = None
        started = False

        def start(self):
            self.started = True

    search_ready = threading.Event()
    local_ai = LocalAI(Server())
    local_ai.start_automatically(after=search_ready.wait)
    time.sleep(0.3)
    assert local_ai.status()["state"] == "idle"  # waits for the search model
    search_ready.set()
    deadline = time.monotonic() + 20
    while local_ai.status()["state"] != "done" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert local_ai.status()["state"] == "done" and llamacpp.installed() and local_ai.server.started


def test_no_download_without_room_for_it(tmp_path, monkeypatch):
    import shutil
    import time

    from backend.llm.local_ai import LocalAI

    monkeypatch.setenv("VAULT_LLM_DIR", str(tmp_path / "llm"))
    monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10**12, 10**12 - 10**9, 10**9))
    local_ai = LocalAI(server=None)
    monkeypatch.setattr(local_ai, "downloadable", lambda: True)
    local_ai.start_automatically()
    deadline = time.monotonic() + 5
    while local_ai.status()["state"] == "idle" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = local_ai.status()
    assert status["state"] == "failed" and "isn't enough free space" in status["error"] and "1.0 GB free" in status["error"]


def test_the_apps_engine_asks_for_the_download_unless_switched_off(monkeypatch):
    from backend.api.app import parse_args
    from backend.engine.bridge import engine_arguments

    monkeypatch.setenv("VAULT_AI_AUTO_DOWNLOAD", "1")
    assert parse_args(engine_arguments(["--no-phones"])).download_ai
    monkeypatch.setenv("VAULT_AI_AUTO_DOWNLOAD", "0")
    assert not parse_args(engine_arguments(["--no-phones"])).download_ai
