"""Step 2: vector-only retrieval baseline on BEIR NQ.

Embeds every chunk from step 1 (cached to disk after the first run), embeds each
test question, retrieves the nearest chunks by cosine similarity, and scores the
ranking against the qrels.

Usage:
    python -m src.eval.evaluate                    # bge-small, writes results/step2_vector.json
    python -m src.eval.evaluate --show-examples 5  # also print a few questions with their top hits

Outputs:
    results/step2_vector.json                  metrics + run config (commit this)
    data/processed/nq/runs/vector.json         per-query top-100 docs (for later comparisons)
    data/processed/nq/embeddings/<model>.npy   cached chunk embeddings
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np

from src.eval.metrics import evaluate, to_doc_ranking
from src.ingestion.loader import load_qrels, load_queries
from src.retrieval.chunk_store import load_chunks, load_or_embed_chunks
from src.retrieval.embedder import DEFAULT_MODEL
from src.retrieval.vector_index import NumpyIndex

logger = logging.getLogger("eval")


def vector_search(
    chunk_embeddings: np.ndarray,
    chunk_doc_ids: list[str],
    query_embeddings: np.ndarray,
    query_ids: list[str],
    top_k: int = 100,
) -> dict[str, list[tuple[str, float]]]:
    """Return {query_id: [(doc_id, score), ...]} with top_k distinct docs per query."""
    # Fetch extra chunks so that after merging chunks of the same doc we still have top_k docs.
    scores, idx = NumpyIndex(chunk_embeddings).search(query_embeddings, top_k=top_k * 2)
    return {
        qid: to_doc_ranking((chunk_doc_ids[i] for i in idx[row]), scores[row], top_k)
        for row, qid in enumerate(query_ids)
    }


def print_examples(run, queries, qrels, titles, n: int) -> None:
    """Show a few questions: the gold passage, where it ranked, and the top 3 hits."""
    print(f"\n=== {n} example questions ===")
    for qid in list(qrels)[:n]:
        ranked = [d for d, _ in run[qid]]
        gold = [d for d, s in qrels[qid].items() if s > 0]
        ranks = [ranked.index(g) + 1 if g in ranked else None for g in gold]
        print(f"\nQ {qid}: {queries[qid]}")
        for g, r in zip(gold, ranks):
            print(f"  gold: {g} {titles.get(g, '?')!r} -> rank {r if r else '>' + str(len(ranked))}")
        for i, (d, s) in enumerate(run[qid][:3], 1):
            mark = "  <- gold" if d in gold else ""
            print(f"  #{i} {s:.3f} {d} {titles.get(d, '?')!r}{mark}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="nq")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--processed-dir", default="data/processed")
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--split", default="test")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default=None, help="cuda / mps / cpu; default picks automatically")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--no-title", action="store_true", help="embed chunk text without its title")
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--show-examples", type=int, default=3)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from src.retrieval.embedder import Embedder  # imports torch; keep it out of module import

    data_dir = Path(args.processed_dir) / args.dataset
    chunks_path = data_dir / "chunks.jsonl"
    chunks = load_chunks(chunks_path)
    chunk_doc_ids = [c["doc_id"] for c in chunks]
    titles = {c["doc_id"]: c["title"] for c in chunks}

    raw_dir = Path(args.raw_dir) / args.dataset
    queries = load_queries(raw_dir)
    qrels = load_qrels(raw_dir, args.split)
    query_ids = [q for q in qrels if q in queries]
    logger.info("%d chunks, %d eval queries", len(chunks), len(query_ids))

    embedder = Embedder(args.model, device=args.device, batch_size=args.batch_size)
    chunk_emb = load_or_embed_chunks(chunks, chunks_path, embedder, data_dir / "embeddings", not args.no_title)

    t0 = time.time()
    query_emb = embedder.encode_queries([queries[q] for q in query_ids])
    run = vector_search(chunk_emb, chunk_doc_ids, query_emb, query_ids, args.top_k)
    search_secs = time.time() - t0

    metrics = evaluate({q: [d for d, _ in hits] for q, hits in run.items()}, qrels)
    result = {
        "step": 2,
        "method": "vector",
        "model": args.model,
        "embed_title": not args.no_title,
        "dataset": args.dataset,
        "split": args.split,
        "num_queries": len(qrels),
        "num_chunks": len(chunks),
        "num_docs": len(titles),
        "metrics": metrics,
        "query_encode_and_search_secs": round(search_secs, 1),
    }
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "step2_vector.json").write_text(json.dumps(result, indent=2) + "\n")
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "vector.json").write_text(json.dumps(run))

    print("\n=== Vector-only retrieval ===")
    for name, value in metrics.items():
        print(f"  {name:<10} {value:.4f}")
    print(f"\nSaved {results_dir / 'step2_vector.json'}")
    if args.show_examples:
        print_examples(run, queries, qrels, titles, args.show_examples)


if __name__ == "__main__":
    main()
