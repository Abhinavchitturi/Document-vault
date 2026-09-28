import numpy as np
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.retrieval.searcher import VectorSearcher
from tests.test_retrieval import FakeEmbedder

CHUNKS = [
    {"doc_id": "d1", "chunk_id": "d1#0", "title": "Eiffel Tower", "text": "wrought iron tower in paris designed by eiffel"},
    {"doc_id": "d2", "chunk_id": "d2#0", "title": "Photosynthesis", "text": "plants convert light energy into chemical energy"},
    {"doc_id": "d2", "chunk_id": "d2#1", "title": "Photosynthesis", "text": "chlorophyll in plants absorbs light"},
    {"doc_id": "d3", "chunk_id": "d3#0", "title": "Great Wall", "text": "ancient wall across northern china"},
]
QUERIES = {"test0": "how do plants convert light into energy", "test1": "who built the great wall of china"}
QRELS = {"test0": {"d2": 1}, "test1": {"d3": 1}}


@pytest.fixture
def client():
    emb = FakeEmbedder()
    searcher = VectorSearcher(CHUNKS, emb.encode_passages([c["title"] + " " + c["text"] for c in CHUNKS]), emb)
    return TestClient(create_app(searcher, QUERIES, QRELS))


def test_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "RAG Search" in res.text


def test_free_text_search_returns_one_hit_per_doc(client):
    data = client.get("/api/search", params={"q": "plants light energy", "k": 3}).json()
    assert data["results"][0]["doc_id"] == "d2"
    assert [r["rank"] for r in data["results"]] == [1, 2, 3]
    assert len({r["doc_id"] for r in data["results"]}) == 3  # d2's two chunks merged
    assert data["gold"] == [] and not any(r["is_gold"] for r in data["results"])


def test_test_question_reports_gold_rank(client):
    data = client.get("/api/search", params={"qid": "test0", "k": 1}).json()
    assert data["question"] == QUERIES["test0"]
    assert data["gold"] == [{"doc_id": "d2", "rank": 1}]
    assert len(data["results"]) == 1 and data["results"][0]["is_gold"]


def test_examples_and_errors(client):
    examples = client.get("/api/examples", params={"n": 5}).json()
    assert {e["qid"] for e in examples} == {"test0", "test1"}
    assert client.get("/api/search").status_code == 400
    assert client.get("/api/search", params={"qid": "nope"}).status_code == 404


def test_searcher_rejects_mismatched_embeddings():
    with pytest.raises(ValueError):
        VectorSearcher(CHUNKS, np.zeros((2, 64), dtype=np.float32), FakeEmbedder())
