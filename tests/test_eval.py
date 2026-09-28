import json
from pathlib import Path

from src.eval.evaluate import build_library, evaluate, load_questions
from src.eval.metrics import answer_at_k, mrr_at_k, recall_at_k
from src.ingestion.chunker import RegexTokenizer
from tests.fakes import FakeEmbedder

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_metrics_by_hand():
    ranked = ["x.pdf", "gold.pdf", "y.pdf"]
    assert recall_at_k(ranked, {"gold.pdf"}, 1) == 0 and recall_at_k(ranked, {"gold.pdf"}, 2) == 1
    assert mrr_at_k(ranked, {"gold.pdf"}, 5) == 0.5
    assert answer_at_k(["nothing", "Expires on 14  March\n2027."], "14 March 2027", 2) == 1
    assert answer_at_k(["nothing", "Expires on 14 March 2027."], "14 March 2027", 1) == 0


def test_every_sample_answer_is_in_its_document():
    """Guards the question set: a typo in an answer would silently score as a miss."""
    files = {p.name: p.read_text() for p in (SAMPLES / "documents").rglob("*.md")}
    for q in load_questions(SAMPLES / "questions.jsonl"):
        for name in q["relevant"]:
            assert name in files, name
            assert " ".join(q["answer"].split()) in " ".join(files[name].split()), (q["question"], name)


def test_evaluate_runs_on_samples(tmp_path):
    emb = FakeEmbedder()
    store = build_library(SAMPLES / "documents", tmp_path / "idx", emb, RegexTokenizer())
    assert len(store.documents) == 13
    assert {d["category"] for d in store.documents.values()} == {"insurance", "warranty", "bill", "property"}
    metrics, details = evaluate(store, emb, load_questions(SAMPLES / "questions.jsonl"))
    assert set(metrics) == {"recall@1", "recall@3", "recall@5", "answer@1", "answer@3", "answer@5", "mrr@5"}
    assert all(0 <= v <= 1 for v in metrics.values()) and len(details) == 24
    assert metrics["recall@5"] >= metrics["recall@1"]
    json.dumps(details)  # serializable for reports
