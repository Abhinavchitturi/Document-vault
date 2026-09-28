"""Phase 5: keyword (BM25) search and hybrid fusion."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.retriever.bm25 import BM25Index, identifiers, tokenize
from backend.retriever.retriever import Retriever, fuse
from backend.upload.ingest import ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder


def chunk(cid, text, filename="doc.pdf", category="other"):
    return {"chunk_id": cid, "doc_id": cid, "filename": filename, "category": category, "text": text}


def test_tokenize_keeps_numbers_and_identifiers_whole():
    tokens = tokenize("Policy KMI/PC/25/0093318: total Rs. 2,486 and 10,00,000; the bills")
    assert "kmi/pc/25/0093318" in tokens and "0093318" in tokens
    assert "2486" in tokens and "1000000" in tokens
    assert "bill" in tokens and "the" not in tokens  # singular, no stopwords


def test_identifiers_are_long_numbers_not_years_or_small_amounts():
    assert identifiers("serial FL340D-2310-44721 bought in 2025 for Rs. 450") == ["fl340d", "44721", "fl340d-2310-44721"]
    assert identifiers("claims helpline 1800-266-7010") == ["1800-266-7010"]
    assert identifiers("when does my bike insurance expire") == []


def test_exact_number_beats_similar_wording():
    chunks = [
        chunk("a", "Two-wheeler policy SGI/TW/2025/301907. Policy period 2025 to 2026.", "bike 2025.pdf"),
        chunk("b", "Renewal of the two-wheeler policy. Policy SGI/TW/2026/448812, period 2026 to 2027.", "bike 2026.pdf"),
        chunk("c", "Refrigerator serial number FL340D-2310-44721."),
    ]
    index = BM25Index(chunks)
    assert index.rank("policy SGI/TW/2026/448812")[0][0]["chunk_id"] == "b"
    assert index.rank("serial FL340D-2310-44721")[0][0]["chunk_id"] == "c"
    assert index.rank("nothing in common zzz") == []
    assert [c["chunk_id"] for c, _ in index.rank("policy", category="bill")] == []


def test_fusion_rewards_agreement_and_keeps_either_top_result():
    a, b, c = chunk("a", ""), chunk("b", ""), chunk("c", "")
    fused = [x["chunk_id"] for x, _ in fuse([[(a, .9), (b, .8), (c, .7)], [(b, 9.0), (c, 5.0)]])]
    assert fused[0] == "b"  # high in both beats first in one
    assert set(fused) == {"a", "b", "c"}


def test_keyword_index_follows_uploads_and_deletes(tmp_path):
    store = DocumentStore(tmp_path / "index")
    emb, tok = FakeEmbedder(), RegexTokenizer()
    retriever = Retriever(store, emb, "keyword")
    (tmp_path / "tv.txt").write_text("Television serial number LV55U7-KX2411-0823")
    assert retriever.documents("LV55U7-KX2411-0823") == []
    doc = ingest_file(tmp_path / "tv.txt", store, emb, tok).document
    assert retriever.documents("LV55U7-KX2411-0823")[0][0]["doc_id"] == doc["doc_id"]
    store.delete(doc["doc_id"])
    assert retriever.documents("LV55U7-KX2411-0823") == []


def test_hybrid_finds_what_vector_search_ranks_low(tmp_path, monkeypatch):
    store = DocumentStore(tmp_path / "index")
    emb, tok = FakeEmbedder(), RegexTokenizer()
    for name, text in [("fridge.txt", "Refrigerator warranty. Serial FL340D-2310-44721. Compressor 10 years."),
                       ("tv.txt", "Television warranty card. Serial LV55U7-KX2411-0823. Panel 2 years.")]:
        (tmp_path / name).write_text(text)
        ingest_file(tmp_path / name, store, emb, tok)
    fridge = next(c for c in store.chunks if c["filename"] == "fridge.txt")
    tv = next(c for c in store.chunks if c["filename"] == "tv.txt")
    # A vector model that (wrongly) prefers the TV for this question:
    monkeypatch.setattr(store, "rank", lambda q, category=None: [(tv, 0.9), (fridge, 0.2)])
    question = "which warranty is FL340D-2310-44721"
    assert Retriever(store, emb, "vector").documents(question)[0][0]["filename"] == "tv.txt"
    assert Retriever(store, emb, "hybrid").documents(question)[0][0]["filename"] == "fridge.txt"


def test_unknown_method_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        Retriever(DocumentStore(tmp_path / "index"), FakeEmbedder(), "magic")


def test_search_endpoint_reports_and_accepts_method(tmp_path):
    client = TestClient(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer()))
    client.post("/api/documents", files=[("files", ("water.txt", b"Water bill. Connection ID PMW-B-0448120. Due 15 July."))])
    r = client.get("/api/search", params={"q": "PMW-B-0448120"}).json()
    assert r["method"] == "hybrid" and r["results"][0]["filename"] == "water.txt"
    assert client.get("/api/search", params={"q": "water", "method": "keyword"}).json()["method"] == "keyword"
    assert client.get("/api/search", params={"q": "water", "method": "magic"}).status_code == 400
    assert client.get("/api/settings").json()["search_method"] == "hybrid"
