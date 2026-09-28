"""Score retrieval on a question set with known answers.

By default this builds a fresh, temporary library from the fictional sample
documents in samples/documents/ (your real library in data/index/ is never
touched), asks every question in samples/questions.jsonl, and reports how often
search put the right document, and the chunk containing the answer, near the top.

Usage:
    python -m backend.eval.evaluate                  # compare hybrid, vector and keyword search
                                                     # -> results/eval_search.json
    python -m backend.eval.evaluate --show-misses    # also list questions that failed
    python -m backend.eval.evaluate --with-llm       # also answer each question with the local LLM,
                                                     # using hybrid search -> results/eval_hybrid_llm.json

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

from backend.eval.metrics import answer_at_k, contains_answer, mrr_at_k, recall_at_k
from backend.llm.answer import answer_question
from backend.parser.categories import CATEGORIES
from backend.retriever.retriever import METHODS, Retriever
from backend.upload.ingest import EmbedderLike, ingest_file, iter_files
from backend.vector_store.store import DocumentStore

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


def evaluate(
    store: DocumentStore, embedder: EmbedderLike, questions: list[dict], ks=KS, llm=None, context_k: int = 5,
    method: str = "hybrid",
) -> tuple[dict, list[dict]]:
    """Return (averaged metrics, per-question details). With `llm`, also score the written answers."""
    retriever = Retriever(store, embedder, method)
    totals = {f"{m}@{k}": 0.0 for m in ("recall", "answer") for k in ks}
    totals[f"mrr@{max(ks)}"] = 0.0
    details = []
    for q in questions:
        hits = retriever.documents(q["question"], top_k=max(ks))
        docs = [chunk["filename"] for chunk, _ in hits]
        texts = [chunk["text"] for chunk, _ in hits]
        relevant = set(q["relevant"])
        for k in ks:
            totals[f"recall@{k}"] += recall_at_k(docs, relevant, k)
            totals[f"answer@{k}"] += answer_at_k(texts, q["answer"], k)
        totals[f"mrr@{max(ks)}"] += mrr_at_k(docs, relevant, max(ks))
        rank = next((i + 1 for i, d in enumerate(docs) if d in relevant), None)
        detail = {"question": q["question"], "relevant": q["relevant"], "rank": rank, "top3": docs[:3],
                  "answer_found@1": bool(answer_at_k(texts, q["answer"], 1))}
        if llm is not None:
            context = [c for c, _ in retriever.context(q["question"], top_k=context_k)]
            written = answer_question(q["question"], context, llm).text
            correct = contains_answer(written, q["answer"])
            totals["answer_accuracy"] = totals.get("answer_accuracy", 0.0) + correct
            detail.update(llm_answer=written, llm_correct=bool(correct))
        details.append(detail)
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
    parser.add_argument("--method", default="all", choices=["all", *METHODS],
                        help="search method to score (default: compare all three)")
    parser.add_argument("--with-llm", action="store_true", help="also answer with the local Ollama model")
    parser.add_argument("--llm-model", default=None, help="Ollama model (default: llama3.1:8b)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from backend.chunker.chunker import HFTokenizer
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder

    llm = None
    if args.with_llm:
        from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
        from backend.llm.ollama import OllamaLLM

        llm = OllamaLLM(args.llm_model or DEFAULT_LLM)
    methods = list(METHODS) if args.method == "all" else [args.method]
    if llm and args.method == "all":
        methods = ["hybrid"]  # answering every question three times isn't worth the wait
    embedder = Embedder(args.model or DEFAULT_MODEL)
    store = build_library(args.docs, args.index_dir, embedder, HFTokenizer(embedder.model_name))
    questions = load_questions(args.questions)

    runs = {}
    for method in methods:
        t0 = time.time()
        metrics, details = evaluate(store, embedder, questions, llm=llm, method=method)
        runs[method] = {"metrics": metrics, "details": details, "seconds": round(time.time() - t0, 1)}

    result = {
        "model": embedder.model_name,
        "llm": llm.name if llm else None,
        "documents": len(store.documents),
        "chunks": len(store.chunks),
        "questions": len(questions),
        "methods": {m: {"metrics": r["metrics"], "seconds": r["seconds"]} for m, r in runs.items()},
    }
    name = f"eval_{methods[0]}_llm.json" if llm else ("eval_search.json" if len(methods) > 1 else f"eval_{methods[0]}.json")
    out = args.results_dir / name
    args.results_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")

    print(f"\n=== {len(questions)} questions over {len(store.documents)} documents ===")
    names = list(next(iter(runs.values()))["metrics"])
    print(f"  {'':<10}" + "".join(f"{m:>10}" for m in runs))
    for metric in names:
        print(f"  {metric:<10}" + "".join(f"{r['metrics'][metric]:>10.3f}" for r in runs.values()))
    for method, run in runs.items():
        details = run["details"]
        misses = [d for d in details if d["rank"] != 1 or not d["answer_found@1"]]
        print(f"\n{method}: {len(misses)} questions where the top result was the wrong document or lacked the answer.")
        if args.show_misses:
            for d in misses:
                print(f"  Q: {d['question']}\n     expected {d['relevant']} -> rank {d['rank']}; top 3: {d['top3']}")
        if llm:
            wrong = [d for d in details if not d["llm_correct"]]
            print(f"{method}: {len(wrong)} written answers missing the expected text.")
            if args.show_misses:
                for d in wrong:
                    print(f"  Q: {d['question']}\n     answer: {d['llm_answer']}")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
