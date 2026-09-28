import json
import math

import numpy as np
import pytest

from src.eval.evaluate import load_or_embed_chunks, vector_search
from src.eval.metrics import evaluate, mrr_at_k, ndcg_at_k, recall_at_k, to_doc_ranking
from src.retrieval.embedder import format_passage, model_prefixes
from src.retrieval.vector_index import NumpyIndex


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def test_numpy_index_returns_best_first():
    emb = unit([[1, 0], [0, 1], [1, 1], [-1, 0]])
    scores, idx = NumpyIndex(emb).search(unit([[1, 0.1]]), top_k=3)
    assert idx[0].tolist() == [0, 2, 1]
    assert scores[0][0] == pytest.approx(max(scores[0]))


def test_to_doc_ranking_keeps_best_chunk_per_doc():
    ranking = to_doc_ranking(["a", "b", "a", "c", "b", "d"], [0.9, 0.8, 0.7, 0.6, 0.5, 0.4], k=3)
    assert ranking == [("a", 0.9), ("b", pytest.approx(0.8)), ("c", pytest.approx(0.6))]


def test_metrics_by_hand():
    rels = {"g1": 1, "g2": 1}
    ranked = ["x", "g1", "y", "g2"]
    assert recall_at_k(ranked, rels, 1) == 0
    assert recall_at_k(ranked, rels, 2) == 0.5
    assert recall_at_k(ranked, rels, 10) == 1
    assert mrr_at_k(ranked, rels, 10) == 0.5  # first gold at rank 2
    assert mrr_at_k(ranked, rels, 1) == 0
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(ranked, rels, 10) == pytest.approx(dcg / idcg)


def test_evaluate_averages_and_counts_missing_queries_as_zero():
    qrels = {"q1": {"d1": 1}, "q2": {"d2": 1}}
    metrics = evaluate({"q1": ["d1", "x"]}, qrels, recall_ks=(10,))
    assert metrics["recall@10"] == 0.5
    assert metrics["mrr@10"] == 0.5


def test_prefixes_and_passage_format():
    assert model_prefixes("BAAI/bge-small-en-v1.5")[0].startswith("Represent this sentence")
    assert model_prefixes("intfloat/e5-base-v2") == ("query: ", "passage: ")
    assert format_passage("Lee Baldwin", "Lee married Gail.") == "Lee Baldwin Lee married Gail."
    assert format_passage("Lee Baldwin", "Lee married Gail.", with_title=False) == "Lee married Gail."


class FakeEmbedder:
    """Bag-of-words hashing embedder: deterministic, no model download."""

    model_name = "fake/bow"

    def __init__(self):
        self.passage_calls = 0

    def _vec(self, text):
        v = np.zeros(64, dtype=np.float32)
        for w in text.lower().split():
            v[hash(w) % 64] += 1
        return v / (np.linalg.norm(v) or 1)

    def encode_passages(self, texts):
        self.passage_calls += 1
        return np.stack([self._vec(t) for t in texts])

    def encode_queries(self, texts):
        return np.stack([self._vec(t) for t in texts])


def test_end_to_end_vector_search_and_cache(tmp_path):
    chunks = [
        {"doc_id": "d1", "title": "Eiffel Tower", "text": "wrought iron tower in paris designed by eiffel"},
        {"doc_id": "d2", "title": "Photosynthesis", "text": "plants convert light energy into chemical energy"},
        {"doc_id": "d2", "title": "Photosynthesis", "text": "chlorophyll absorbs light in plants"},
        {"doc_id": "d3", "title": "Great Wall", "text": "ancient wall across northern china"},
    ]
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("".join(json.dumps(c) + "\n" for c in chunks))
    emb = FakeEmbedder()
    chunk_emb = load_or_embed_chunks(chunks, chunks_path, emb, tmp_path / "emb", with_title=True)
    load_or_embed_chunks(chunks, chunks_path, emb, tmp_path / "emb", with_title=True)
    assert emb.passage_calls == 1  # second call hit the cache
    load_or_embed_chunks(chunks, chunks_path, emb, tmp_path / "emb", with_title=False)
    assert emb.passage_calls == 2  # different passage format invalidates it

    run = vector_search(
        chunk_emb,
        [c["doc_id"] for c in chunks],
        emb.encode_queries(["how do plants convert light energy", "tower in paris"]),
        ["q1", "q2"],
        top_k=3,
    )
    assert run["q1"][0][0] == "d2" and run["q2"][0][0] == "d1"
    assert len({d for d, _ in run["q1"]}) == len(run["q1"]) == 3  # d2's two chunks merged
