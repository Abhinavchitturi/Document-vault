"""Adding a document never waits for the private AI; the AI takes a closer look afterwards (backend/extract/refine.py).
And archives (zip, 7z…) are turned away with a clear reason."""
import io
import threading
import time
import zipfile

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

LETTER = ("Dear customer, thank you for your purchase from Croma. Reference number CR-55120. "
          "Your extended cover for the washing machine is valid until 2 January 2029.")


class SlowAI:
    """A private AI that takes its time (like one on a laptop without a fast graphics chip), and is sure of itself."""

    name = "llama.cpp/slow"

    def __init__(self):
        self.calls, self.answering = 0, threading.Event()
        self.ready = True

    def status(self):
        return self.ready, "ready" if self.ready else "starting"

    def generate_json(self, system, prompt, schema):
        self.calls += 1
        self.answering.set()
        time.sleep(0.3)
        if "sort a family's documents" in system:
            return {"category": "warranty", "confidence": "high"}
        return {"expires_on": "2029-01-02", "due_date": None, "amount": None, "reference": "CR-55120", "issuer": "Croma"}


def upload(client, name, content):
    return client.post("/api/documents", files=[("files", (name, content))], data={"category": "auto"}).json()


def test_adding_doesnt_wait_for_the_ai_which_looks_closer_afterwards(tmp_path):
    ai = SlowAI()
    store = DocumentStore(tmp_path / "index")
    client = signed_in(create_app(store, FakeEmbedder(), RegexTokenizer(), ai, frontend_dir=None))
    [res] = upload(client, "letter.txt", LETTER.encode())
    assert res["status"] == "added"
    assert res["document"]["facts_method"] != "llm"  # filed by the quick readers, straight away

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        [doc] = client.get("/api/documents").json()
        if doc["facts_method"] == "llm":
            break
        time.sleep(0.1)
    assert doc["facts_method"] == "llm" and doc["reference"] == "CR-55120" and doc["issuer"] == "Croma"
    assert doc["expires_on"] == "2029-01-02"


def test_the_closer_look_leaves_what_a_person_changed(tmp_path):
    ai = SlowAI()
    ai.ready = False  # still starting when the document is added
    store = DocumentStore(tmp_path / "index")
    client = signed_in(create_app(store, FakeEmbedder(), RegexTokenizer(), ai, frontend_dir=None))
    [res] = upload(client, "letter.txt", LETTER.encode())
    doc_id = res["document"]["doc_id"]
    assert client.patch(f"/api/documents/{doc_id}", json={"category": "other", "reference": "MINE-1"}).status_code == 200
    from backend.extract import refine

    user_id = client.get("/api/auth/status").json()["user"]["id"]
    view = store.for_user(user_id)
    item = refine._Item(view, doc_id, "letter.txt", LETTER)
    r = refine.Refiner(ai, FakeEmbedder(), lambda chunks: [c["text"] for c in chunks])
    ai.ready = True
    r.refine(item)
    [doc] = client.get("/api/documents").json()
    assert doc["category"] == "other" and doc["reference"] == "MINE-1"  # the person's choices stay


def _zip(names):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in names:
            z.writestr(name, "%PDF-1.4 hello")
    return out.getvalue()


def test_archives_are_turned_away_with_a_reason(tmp_path):
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), frontend_dir=None))
    for name, content in [("family.zip", _zip(["a.pdf"])), ("photos", _zip(["b.jpg"])),
                          ("old.7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 40), ("box.rar", b"Rar!\x1a\x07\x00" + b"\0" * 40),
                          ("backup.tar.gz", b"\x1f\x8b\x08" + b"\0" * 40)]:
        [res] = upload(client, name, content)
        assert res["status"] == "error" and "Unzip it first" in res["error"], (name, res)
    assert client.get("/api/documents").json() == []
