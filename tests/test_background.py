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


def test_download_progress_is_measured_in_the_models_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_SEARCH_MODEL_DIR", str(tmp_path))
    assert not download.downloaded() and download._measured() == 0
    (tmp_path / ".cache" / "huggingface" / "download").mkdir(parents=True)
    (tmp_path / ".cache" / "huggingface" / "download" / "abc.incomplete").write_bytes(b"x" * 1000)  # part way
    (tmp_path / "tokenizer.json").write_bytes(b"x" * download.FILES["tokenizer.json"])
    assert download._measured() == download.FILES["tokenizer.json"] + 1000
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx" / "model.onnx").write_bytes(b"")  # wrong size: not downloaded yet
    assert not download.downloaded()


def test_earlier_copies_are_removed_without_following_their_links(tmp_path, monkeypatch):
    """Before 1.0.8 the model was PyTorch's, in the Hugging Face cache, whose links Windows refused to follow
    ("[WinError 448] ... untrusted mount point") even to check a file was there."""
    folder, hub = tmp_path / "model", tmp_path / "hub"
    monkeypatch.setenv("VAULT_SEARCH_MODEL_DIR", str(folder))
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    repo = hub / f"models--{download.DEFAULT_MODEL.replace('/', '--')}"
    (repo / "snapshots" / download.REVISION).mkdir(parents=True)
    (repo / "snapshots" / download.REVISION / "model.safetensors").symlink_to(tmp_path / "elsewhere")  # dangling
    for name, size in download.FILES.items():  # this version's files, already here
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x" * size)
    (folder / "model.safetensors").write_bytes(b"old")  # 1.0.7's copy
    (folder / "1_Pooling").mkdir()
    assert download.ensure() == folder
    assert not repo.exists() and not (folder / "model.safetensors").exists() and not (folder / "1_Pooling").exists()
    assert download.downloaded()


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


def test_adding_a_document_never_waits_for_the_search_model(tmp_path):
    """A slow computer's first minutes: the document is kept at once and becomes searchable when the model loads."""
    from backend.api.app import create_app
    from backend.chunker.chunker import RegexTokenizer
    from backend.vector_store.store import DocumentStore
    from tests.fakes import FakeEmbedder, signed_in

    go = threading.Event()

    def make():
        go.wait(30)
        return FakeEmbedder()

    embedder = InBackground("search model", make, model_name="fake")
    store = DocumentStore(tmp_path / "index")
    client = signed_in(create_app(store, embedder, RegexTokenizer(), None, frontend_dir=None))
    started = time.monotonic()
    text = b"Two-wheeler insurance policy TW-778812. Premium Rs 2,450. Period 15 March 2026 to 14 March 2027."
    [res] = client.post("/api/documents", files=[("files", ("bike.txt", text))], data={"category": "auto"}).json()
    assert res["status"] == "added" and time.monotonic() - started < 5
    assert store.missing_vectors  # kept, not yet searchable
    go.set()
    deadline = time.monotonic() + 10
    while store.missing_vectors and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not store.missing_vectors and store.num_vectors > 0
    hits = client.get("/api/search", params={"q": "bike insurance"}).json()
    assert any(h["filename"] == "bike.txt" for h in (hits if isinstance(hits, list) else hits.get("results", [])))
