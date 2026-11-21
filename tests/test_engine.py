"""The native apps' engine (backend/engine): the vault over a private pipe, one JSON object per line, no network."""

from __future__ import annotations

import base64
import json

import pytest

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.engine.bridge import Pipe, serve
from backend.vector_store.store import DocumentStore
from tests.fakes import PASSWORD, FakeEmbedder, FakeLLM


class Lines:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text):
        self.lines.extend(line for line in text.splitlines() if line)

    def flush(self):
        pass

    def messages(self):
        return [json.loads(line) for line in self.lines]


def talk(app, *requests):
    """Send requests through the pipe as the Mac app would; return every message the engine sent back."""
    out = Lines()
    serve(app, Pipe(out), [json.dumps(r) for r in requests])
    return out.messages()


def answer(messages, rid):
    return next(m for m in messages if m.get("id") == rid and "status" in m)


@pytest.fixture
def app(tmp_path):
    return create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(),
                      FakeLLM("Your bike insurance expires on 14 March 2027 [1]."), frontend_dir=None)


def sign_in(app):
    """Set up the vault through the pipe; return the session cookie the app would keep in the Keychain."""
    setup = answer(talk(app, {"id": 1, "method": "POST", "path": "/api/auth/setup",
                              "json": {"name": "abhinav", "password": PASSWORD}}), 1)
    assert setup["status"] == 200 and setup["json"]["user"]["name"] == "abhinav"
    cookie = setup["headers"]["set-cookie"].split(";")[0]
    assert cookie.startswith("vault_session=") and "HttpOnly" in setup["headers"]["set-cookie"]
    return {"cookie": cookie}


def test_json_answers_and_the_sign_in_the_app_keeps(app):
    status = answer(talk(app, {"id": 1, "method": "GET", "path": "/api/auth/status"}), 1)
    assert status["status"] == 200 and status["json"]["setup_needed"] is True
    headers = sign_in(app)
    me = talk(app, {"id": 2, "method": "GET", "path": "/api/auth/status", "headers": headers},
              {"id": 3, "method": "GET", "path": "/api/documents"})  # no cookie: not signed in
    assert answer(me, 2)["json"]["user"]["name"] == "abhinav"
    assert answer(me, 3)["status"] == 401


def test_upload_by_path_stream_a_chat_answer_and_fetch_the_original(app, tmp_path):
    headers = sign_in(app)
    policy = tmp_path / "Bike Insurance 2026.txt"
    policy.write_text("Two-wheeler policy. Period: 15 March 2026 to 14 March 2027.")
    uploaded = answer(talk(app, {"id": 4, "method": "POST", "path": "/api/documents", "headers": headers,
                                 "form": {"category": "auto"}, "files": [{"field": "files", "path": str(policy)}]}), 4)
    assert uploaded["status"] == 200 and uploaded["json"][0]["status"] == "added"
    doc_id = uploaded["json"][0]["document"]["doc_id"]

    chat = talk(app, {"id": 5, "method": "POST", "path": "/api/chat", "headers": headers,
                      "json": {"question": "When does my bike insurance expire?"}})
    start = answer(chat, 5)
    assert start["stream"] is True and start["headers"]["content-type"].startswith("application/x-ndjson")
    events = [json.loads(m["chunk"]) for m in chat if m.get("id") == 5 and "chunk" in m]
    assert events[0]["type"] == "sources" and events[-1]["type"] == "done" and events[-1]["citations"] == [1]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Your bike insurance expires on 14 March 2027 [1]."
    assert chat[-1] == {"id": 5, "end": True}

    original = answer(talk(app, {"id": 6, "method": "GET", "path": f"/api/documents/{doc_id}/file", "headers": headers}), 6)
    assert original["status"] == 200 and base64.b64decode(original["base64"]) == policy.read_bytes()


def test_many_requests_at_once_each_answered_by_id(app):
    headers = sign_in(app)
    messages = talk(app, *[{"id": n, "method": "GET", "path": "/api/info", "headers": headers} for n in range(10, 20)])
    assert sorted(m["id"] for m in messages if "status" in m) == list(range(10, 20))
    assert all(answer(messages, n)["status"] == 200 for n in range(10, 20))


def test_bad_requests_get_an_answer_not_a_crash(app, tmp_path):
    headers = sign_in(app)
    out = Lines()
    serve(app, Pipe(out), ["not json", json.dumps({"id": 7, "method": "POST", "path": "/api/documents", "headers": headers,
                                                  "files": [{"path": str(tmp_path / "missing.pdf")}]})])
    messages = out.messages()
    assert messages[0]["event"] == "error"
    assert answer(messages, 7)["status"] == 400 and "Can't read the file" in answer(messages, 7)["json"]["detail"]
