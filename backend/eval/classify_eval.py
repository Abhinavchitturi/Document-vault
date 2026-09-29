"""How well does the vault sort documents into types? (Phase 8)

    python -m backend.eval.classify_eval             # keywords, meaning, and both (no assistant)
    python -m backend.eval.classify_eval --with-llm  # also the full sorter, asking Ollama when unsure

Uses the fictional samples (samples/documents/<type>/*.md: the folder is the right answer).
File names are hidden by default ("document.pdf"), because real uploads are often called
"scan001.jpg": the sorter must read the content. Writes results/classify_eval.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backend.classify.classifier import REVIEW_BELOW, Classifier, keyword_scores
from backend.parser.categories import CATEGORIES


def load_samples(folder: Path) -> list[dict]:
    return [{"name": p.name, "category": p.parent.name, "text": p.read_text(encoding="utf-8")}
            for p in sorted(folder.rglob("*.md")) if p.parent.name in CATEGORIES]


def score(samples: list[dict], predict, hide_names: bool) -> dict:
    right, review, wrong, started = 0, 0, [], time.perf_counter()
    for s in samples:
        name = "document.pdf" if hide_names else s["name"]
        category, confidence = predict(name, s["text"])
        right += category == s["category"]
        review += confidence is not None and confidence < REVIEW_BELOW
        if category != s["category"]:
            wrong.append(f"{s['name']}: {s['category']} -> {category}")
    return {"accuracy": round(right / len(samples), 3), "flagged_for_review": review, "wrong": wrong,
            "ms_per_document": round(1000 * (time.perf_counter() - started) / len(samples), 1)}


def evaluate(samples: list[dict], embedder=None, llm=None, hide_names: bool = True) -> dict:
    results = {}
    kw_only = lambda n, t: (max((s := keyword_scores(n, t)), key=s.get), None)  # noqa: E731
    results["keywords"] = score(samples, kw_only, hide_names)
    if embedder is not None:
        sorter = Classifier(embedder)
        meaning = lambda n, t: (max((s := sorter.meaning_scores(n, t)), key=s.get), None)  # noqa: E731
        results["meaning"] = score(samples, meaning, hide_names)
        results["keywords+meaning"] = score(samples, lambda n, t: _pair(sorter.classify(n, t)), hide_names)
    if llm is not None:
        full = Classifier(embedder, llm)
        results["full (with assistant)"] = score(samples, lambda n, t: _pair(full.classify(n, t)), hide_names)
    return results


def _pair(decision):
    return decision.category, decision.confidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, default=Path("samples/documents"))
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--show-names", action="store_true", help="let the sorter see the real file names")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()

    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder

    llm = None
    if args.with_llm:
        from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
        from backend.llm.ollama import OllamaLLM

        llm = OllamaLLM(DEFAULT_LLM)
    samples = load_samples(args.docs)
    results = evaluate(samples, Embedder(DEFAULT_MODEL), llm, hide_names=not args.show_names)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "classify_eval.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n=== Sorting {len(samples)} documents ({'names hidden' if not args.show_names else 'with names'}) ===")
    print(f"  {'method':<24}{'correct':>9}{'flagged':>9}{'ms/doc':>9}")
    for method, r in results.items():
        print(f"  {method:<24}{r['accuracy']:>9.0%}{r['flagged_for_review']:>9}{r['ms_per_document']:>9}")
    for method, r in results.items():
        for w in r["wrong"]:
            print(f"  {method}: {w}")


if __name__ == "__main__":
    main()
