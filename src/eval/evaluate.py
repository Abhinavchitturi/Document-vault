"""Score retrieval on a question set with known answers.

By default this builds a fresh, temporary library from the fictional sample
documents in samples/documents/ (your real library in data/index/ is never
touched), asks every question in samples/questions.jsonl, and reports how often
search put the right document, and the chunk containing the answer, near the top.

Usage:
    python -m src.eval.evaluate                  # writes results/eval_vector.json
    python -m src.eval.evaluate --show-misses    # also list questions that failed

Question file format (one JSON object per line):
    {"question": "...", "relevant": ["file_name.pdf"], "answer": "exact text in the document"}
You can write a question file about your own documents and pass --docs/--questions.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from pathlib import Path

from src.eval.metrics import answer_at_k, mrr_at_k, recall_at_k
from src.ingestion.categories import CATEGORIES
from src.ingestion.ingest import EmbedderLike, ingest_file, iter_files
from src.retrieval.document_store import DocumentStore

logger = logging.getLogger("eval")

KS = (1, 3, 5)


def load_questions(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_library(docs_dir: Path, index_dir: Path, embedder: EmbedderLike, tokenizer) -> DocumentStore:
    """Fresh library from a folder; subfolder names (insurance/, bill/...) become categories."""
    shutil.rmtree(index_dir, ignore_errors=True)
    store = DocumentStore(index_dir)
    for path in iter_files([docs_dir]):
        category = path.parent.name if path.parent.name in CATEGORIES else "auto"
        ingest_file(path, store, embedder, tokenizer, category)
    return store


def evaluate(store: DocumentStore, embedder: EmbedderLike, questions: list[dict], ks=KS) -> tuple[dict, list[dict]]:
    """Return (averaged metrics, per-question details)."""
    query_embs = embedder.encode_queries([q["question"] for q in questions])
    totals = {f"{m}@{k}": 0.0 for m in ("recall", "answer") for k in ks}
    totals[f"mrr@{max(ks)}"] = 0.0
    details = []
    for q, emb in zip(questions, query_embs):
        hits = store.search(emb, top_k=max(ks))
        docs = [chunk["filename"] for chunk, _ in hits]
        texts = [chunk["text"] for chunk, _ in hits]
        relevant = set(q["relevant"])
        for k in ks:
            totals[f"recall@{k}"] += recall_at_k(docs, relevant, k)
            totals[f"answer@{k}"] += answer_at_k(texts, q["answer"], k)
        totals[f"mrr@{max(ks)}"] += mrr_at_k(docs, relevant, max(ks))
        rank = next((i + 1 for i, d in enumerate(docs) if d in relevant), None)
        details.append({"question": q["question"], "relevant": q["relevant"], "rank": rank, "top3": docs[:3],
                        "answer_found@1": bool(answer_at_k(texts, q["answer"], 1))})
    metrics = {name: round(total / len(questions), 4) for name, total in totals.items()}
    return metrics, details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, default=Path("samples/documents"))
    parser.add_argument("--questions", type=Path, default=Path("samples/questions.jsonl"))
    parser.add_argument("--index-dir", type=Path, default=Path("data/eval_index"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from src.ingestion.chunker import HFTokenizer
    from src.retrieval.embedder import DEFAULT_MODEL, Embedder

    embedder = Embedder(args.model or DEFAULT_MODEL)
    t0 = time.time()
    store = build_library(args.docs, args.index_dir, embedder, HFTokenizer(embedder.model_name))
    questions = load_questions(args.questions)
    metrics, details = evaluate(store, embedder, questions)

    result = {
        "method": "vector",
        "model": embedder.model_name,
        "documents": len(store.documents),
        "chunks": len(store.chunks),
        "questions": len(questions),
        "metrics": metrics,
        "seconds": round(time.time() - t0, 1),
    }
    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "eval_vector.json").write_text(json.dumps(result, indent=2) + "\n")

    print(f"\n=== Vector search: {len(questions)} questions over {len(store.documents)} documents ===")
    for name, value in metrics.items():
        print(f"  {name:<10} {value:.3f}")
    misses = [d for d in details if d["rank"] != 1 or not d["answer_found@1"]]
    print(f"\n{len(misses)} questions where the top result was the wrong document or lacked the answer.")
    if args.show_misses:
        for d in misses:
            print(f"\n  Q: {d['question']}\n     expected {d['relevant']} -> rank {d['rank']}; top 3: {d['top3']}")
    print(f"\nSaved {args.results_dir / 'eval_vector.json'}")


if __name__ == "__main__":
    main()
