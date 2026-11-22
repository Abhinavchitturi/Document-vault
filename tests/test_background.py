"""The vault opens at once while the search model downloads and loads beside it, with measured progress."""
import threading
import time

import pytest

from backend.embeddings import download
from backend.embeddings.background import InBackground


def test_answers_known_attributes_at_once_and_waits_for_the_rest():
    go = threading.Event()

    class Model:
        def encode(self, x):
            return x * 2

    def make():
        go.wait(5)
        return Model()

    thing = InBackground("search model", make, model_name="m")
    assert thing.model_name == "m" and not thing.ready and thing.failure is None
    go.set()
    assert thing.encode(21) == 42 and thing.ready


def test_a_failure_is_reported_and_raised_on_use():
    thing = InBackground("search model", lambda: 1 / 0)
    with pytest.raises(RuntimeError, match="couldn't load"):
        thing.encode
    assert not thing.ready and "division" in thing.failure


def test_download_progress_is_measured_from_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    repo = tmp_path / f"models--{download.DEFAULT_MODEL.replace('/', '--')}"
    snapshot = repo / "snapshots" / download.REVISION
    (snapshot / "1_Pooling").mkdir(parents=True)
    (repo / "blobs").mkdir()
    assert not download.downloaded() and download._measured(download.DEFAULT_MODEL) == 0
    (repo / "blobs" / "abc.incomplete").write_bytes(b"x" * 1000)  # the big file, part way
    for name, size in download.FILES.items():
        if name != "model.safetensors":
            (snapshot / name).write_bytes(b"x" * size)
    small = download.TOTAL - download.FILES["model.safetensors"]
    assert download._measured(download.DEFAULT_MODEL) == small + 1000
    (snapshot / "model.safetensors").write_bytes(b"")  # wrong size: not downloaded yet
    assert not download.downloaded()


def test_the_apps_see_downloading_then_ready(tmp_path):
    from backend.api.app import create_app
    from backend.chunker.chunker import RegexTokenizer
    from backend.vector_store.store import DocumentStore
    from tests.fakes import FakeEmbedder, signed_in

    go = threading.Event()

    def make():
        download.progress.state, download.progress.done, download.progress.total = "downloading", 10, 100
        go.wait(5)
        return FakeEmbedder()

    embedder = InBackground("search model", make, model_name="fake")
    app = create_app(DocumentStore(tmp_path / "index"), embedder, RegexTokenizer(), None, frontend_dir=None)
    client = signed_in(app)
    deadline = time.monotonic() + 5
    while client.get("/api/search/status").json()["state"] != "downloading" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = client.get("/api/search/status").json()
    assert status["state"] == "downloading" and (status["done"], status["total"]) == (10, 100)
    go.set()
    embedder.wait()
    assert client.get("/api/search/status").json()["state"] == "ready"
    download.progress.state = "loading"


def test_the_same_file_added_twice_at_once_counts_once(tmp_path, monkeypatch):
    """Two uploads of one file racing (a retried request): the second finds the first, no error."""
    from backend.chunker.chunker import RegexTokenizer
    from backend.upload import ingest
    from backend.vector_store.store import DocumentStore
    from tests.fakes import FakeEmbedder

    store = DocumentStore(tmp_path / "index")
    doc = tmp_path / "Bike Insurance 2026.txt"
    doc.write_text("Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.")
    first = ingest.ingest_file(doc, store, FakeEmbedder(), RegexTokenizer(), "auto")
    assert first.created
    # the other upload checked before the first was saved: it gets past the check and clashes on saving
    monkeypatch.setattr(store, "find_by_sha256", lambda sha, real=store.find_by_sha256, seen=[]: seen.append(1) or (None if len(seen) == 1 else real(sha)))
    second = ingest.ingest_file(doc, store, FakeEmbedder(), RegexTokenizer(), "auto")
    assert not second.created and second.document["doc_id"] == first.document["doc_id"]
    assert len(store.list_documents()) == 1
