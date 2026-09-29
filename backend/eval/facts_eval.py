"""How well are key facts read? (Phase 9)

    python -m backend.eval.facts_eval                 # every reader that's available
    python -m backend.eval.facts_eval --pdfs          # read the sample PDFs instead of their text

The answers are in samples/facts.jsonl (expiry, due date, amount, reference, issuer for the
13 fictional documents; a null means the document has no such fact, so reading one counts as a
mistake). Writes results/facts_eval.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backend.extract.facts import FIELDS, NeedleReader, OllamaReader, RulesReader, grounded

HERE = Path(__file__).resolve().parents[2] / "samples"


def load_cases(pdfs: bool = False) -> list[tuple[dict, str]]:
    cases = []
    by_stem = {p.stem.lower().replace(" ", "_"): p for p in (HERE / "pdfs").glob("*/*.pdf")}
    for line in (HERE / "facts.jsonl").read_text().splitlines():
        gold = json.loads(line)
        if pdfs:
            from backend.parser.extract import extract_pages

            text = "\n".join(p.text for p in extract_pages(by_stem[Path(gold["file"]).stem]))
        else:
            text = (HERE / "documents" / gold["file"]).read_text(encoding="utf-8")
        cases.append((gold, text))
    return cases


def correct(field: str, want: str | None, got) -> bool:
    if want is None:  # the document has no such fact: reading one is a mistake
        return got is None
    if got is None:
        return False
    if field == "issuer":
        return want.lower() in str(got).lower()
    return str(got).strip().lower() == want.lower()


def score(reader, cases) -> dict:
    right, total, misses, started = 0, 0, [], time.perf_counter()
    for gold, text in cases:
        facts = grounded(reader.read(text), text)
        for field in FIELDS:
            if field in gold:
                total += 1
                if correct(field, gold[field], getattr(facts, field)):
                    right += 1
                else:
                    misses.append(f"{gold['file']} {field}: wanted {gold[field]}, got {getattr(facts, field)}")
    return {"accuracy": round(right / total, 3), "right": right, "total": total, "misses": misses,
            "ms_per_document": round(1000 * (time.perf_counter() - started) / len(cases), 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdfs", action="store_true")
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    args = parser.parse_args()

    readers = {"rules": RulesReader()}
    try:
        readers["needle"] = NeedleReader()
    except ImportError:
        print("Needle 3 isn't installed (pip install cactus-needle): skipping it.")
    from backend.llm.ollama import DEFAULT_MODEL, OllamaLLM

    llm = OllamaLLM(DEFAULT_MODEL)
    if llm.status()[0]:
        readers["ollama"] = OllamaReader(llm)
    else:
        print("Ollama isn't running: skipping the answer model.")

    cases = load_cases(args.pdfs)
    results = {}
    for name, reader in readers.items():
        try:
            results[name] = score(reader, cases)
        except Exception as e:  # e.g. Needle's files not downloaded yet
            print(f"{name} failed: {e}")
    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "facts_eval.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n=== Key facts from {len(cases)} documents ({'PDFs' if args.pdfs else 'text'}) ===")
    print(f"  {'reader':<10}{'correct':>10}{'ms/doc':>10}")
    for name, r in results.items():
        print(f"  {name:<10}{r['right']:>5}/{r['total']:<4}{r['ms_per_document']:>10}")
    for name, r in results.items():
        for m in r["misses"]:
            print(f"  {name}: {m}")


if __name__ == "__main__":
    main()
