"""Step 1 pipeline: download BEIR NQ, select a corpus subset, chunk, write chunks.jsonl.

Usage:
    python -m src.ingestion.run                      # 100k-doc subset, bge tokenizer
    python -m src.ingestion.run --max-docs 0         # full corpus (~2.68M passages)
    python -m src.ingestion.run --show-samples 5     # print a few chunks afterwards
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import statistics
from pathlib import Path

from src.ingestion.chunker import DEFAULT_TOKENIZER, HFTokenizer, RegexTokenizer, chunk_documents
from src.ingestion.download import download_beir_dataset
from src.ingestion.loader import load_qrels, load_queries, select_subset

logger = logging.getLogger("ingestion")


def write_chunks(chunks, path: Path) -> dict:
    """Write chunks as JSONL and return summary stats."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_tokens, per_doc = [], {}
    with open(path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
            n_tokens.append(chunk.n_tokens)
            per_doc[chunk.doc_id] = chunk.num_chunks
    if not n_tokens:
        raise RuntimeError("no chunks produced; is the corpus empty?")
    return {
        "num_docs": len(per_doc),
        "num_chunks": len(n_tokens),
        "multi_chunk_docs": sum(1 for n in per_doc.values() if n > 1),
        "tokens_per_chunk": {
            "min": min(n_tokens),
            "median": statistics.median(n_tokens),
            "mean": round(statistics.mean(n_tokens), 1),
            "max": max(n_tokens),
        },
    }


def show_samples(chunks_path: Path, n: int, seed: int = 0) -> None:
    """Print n random chunks, plus every chunk of one multi-chunk doc if there is one."""
    with open(chunks_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    rng = random.Random(seed)
    print(f"\n=== {n} random chunks ===")
    for row in rng.sample(rows, min(n, len(rows))):
        _print_chunk(row)

    multi = [r for r in rows if r["num_chunks"] > 1]
    if multi:
        doc_id = rng.choice(multi)["doc_id"]
        print(f"\n=== all chunks of multi-chunk doc {doc_id} (check the overlap) ===")
        for row in (r for r in rows if r["doc_id"] == doc_id):
            _print_chunk(row)


def _print_chunk(row: dict) -> None:
    print(
        f"\n--- {row['chunk_id']} | source={row['source']} | title={row['title']!r} | "
        f"chunk {row['chunk_index'] + 1}/{row['num_chunks']} | "
        f"chars {row['start_char']}-{row['end_char']} | {row['n_tokens']} tokens"
    )
    print(row["text"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="nq")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--out-dir", default="data/processed")
    parser.add_argument("--split", default="test", help="qrels split used to pick must-keep docs")
    parser.add_argument("--max-docs", type=int, default=100_000, help="0 = full corpus")
    parser.add_argument("--chunk-size", type=int, default=400)
    parser.add_argument("--overlap", type=int, default=80)
    parser.add_argument("--tokenizer", default=DEFAULT_TOKENIZER, help='HF model name, or "regex"')
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--show-samples", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    dataset_dir = download_beir_dataset(args.dataset, args.raw_dir)
    queries = load_queries(dataset_dir)
    qrels = load_qrels(dataset_dir, args.split)
    logger.info("Loaded %d queries, %d with %s qrels", len(queries), len(qrels), args.split)

    docs = select_subset(dataset_dir, qrels, args.max_docs or None, seed=args.seed)
    tokenizer = RegexTokenizer() if args.tokenizer == "regex" else HFTokenizer(args.tokenizer)
    chunks = chunk_documents(docs, tokenizer, f"beir/{args.dataset}", args.chunk_size, args.overlap)

    out_dir = Path(args.out_dir) / args.dataset
    chunks_path = out_dir / "chunks.jsonl"
    stats = write_chunks(chunks, chunks_path)
    stats.update(
        dataset=args.dataset,
        max_docs=args.max_docs,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        tokenizer=tokenizer.name,
    )
    (out_dir / "chunk_stats.json").write_text(json.dumps(stats, indent=2))
    logger.info("Wrote %s\n%s", chunks_path, json.dumps(stats, indent=2))

    if args.show_samples:
        show_samples(chunks_path, args.show_samples)


if __name__ == "__main__":
    main()
