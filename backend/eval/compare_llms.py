"""Which local LLM answers best? Ask each one the same sample questions and rank them.

Retrieval runs once and every model gets exactly the same passages, so the
comparison measures the LLM alone. Models that aren't downloaded are reported
and skipped.

Usage (each model must be pulled first, e.g. `ollama pull gemma3:12b`):
    python -m backend.eval.compare_llms gemma3:12b llama3.1:8b
    python -m backend.eval.compare_llms llama3.1:8b gemma3:12b --show-misses

Writes results/llm_comparison.json. Accuracy is strict (the answer must contain
the exact expected text), so treat 1-2 questions of difference as a tie: there
are only 24 questions.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from backend.eval.evaluate import build_library, load_questions
from backend.eval.metrics import contains_answer
from backend.llm.answer import answer_question
from backend.llm.ollama import LLMUnavailableError

logger = logging.getLogger("compare")


def compare(questions: list[dict], contexts: list[list[dict]], llms: list) -> list[dict]:
    """One row per model: accuracy, speed and per-question answers. `contexts[i]` are question i's passages."""
    rows = []
    for llm in llms:
        try:
            llm.generate("Reply with OK.", "OK?")  # load the model first so it isn't counted in the timing
        except LLMUnavailableError as e:
            rows.append({"model": llm.name, "available": False, "error": str(e)})
            continue
        answers, seconds = [], []
        for q, passages in zip(questions, contexts):
            t0 = time.perf_counter()
            answer = answer_question(q["question"], passages, llm)
            seconds.append(time.perf_counter() - t0)
            answers.append({"question": q["question"], "expected": q["answer"], "answer": answer.text,
                            "correct": bool(contains_answer(answer.text, q["answer"])), "cited": answer.citations})
        correct = sum(a["correct"] for a in answers)
        rows.append({
            "model": llm.name,
            "available": True,
            "accuracy": round(correct / len(questions), 4),
            "correct": correct,
            "questions": len(questions),
            "cited_a_source": sum(bool(a["cited"]) for a in answers),
            "avg_seconds": round(sum(seconds) / len(seconds), 2),
            "answers": answers,
        })
    rows.sort(key=lambda r: (not r["available"], -r.get("accuracy", 0), r.get("avg_seconds", 0)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="+", help="Ollama model names, e.g. llama3.1:8b gemma3:12b")
    parser.add_argument("--docs", type=Path, default=Path("samples/documents"))
    parser.add_argument("--questions", type=Path, default=Path("samples/questions.jsonl"))
    parser.add_argument("--index-dir", type=Path, default=Path("data/eval_index"))
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--context-k", type=int, default=5, help="passages given to each model")
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from backend.chunker.chunker import HFTokenizer
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder
    from backend.llm.ollama import OllamaLLM

    embedder = Embedder(DEFAULT_MODEL)
    store = build_library(args.docs, args.index_dir, embedder, HFTokenizer(embedder.model_name))
    questions = load_questions(args.questions)
    query_embs = embedder.encode_queries([q["question"] for q in questions])
    contexts = [[c for c, _ in store.search(e, top_k=args.context_k, per_document=False)] for e in query_embs]

    rows = compare(questions, contexts, [OllamaLLM(m) for m in args.models])
    args.results_dir.mkdir(parents=True, exist_ok=True)
    out = args.results_dir / "llm_comparison.json"
    out.write_text(json.dumps({"embedding_model": embedder.model_name, "context_k": args.context_k, "results": rows},
                              indent=2) + "\n")

    print(f"\n=== {len(questions)} questions, same {args.context_k} passages for every model ===\n")
    print(f"{'model':<28} {'accuracy':>9} {'correct':>8} {'cited':>6} {'sec/answer':>11}")
    for r in rows:
        if not r["available"]:
            print(f"{r['model']:<28}  not available: {r['error']}")
            continue
        print(f"{r['model']:<28} {r['accuracy']:>9.3f} {r['correct']:>5}/{r['questions']:<2} "
              f"{r['cited_a_source']:>6} {r['avg_seconds']:>11.1f}")
    if args.show_misses:
        for r in rows:
            for a in (a for a in r.get("answers", []) if not a["correct"]):
                print(f"\n[{r['model']}] {a['question']}\n  expected: {a['expected']}\n  got:      {a['answer']}")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
