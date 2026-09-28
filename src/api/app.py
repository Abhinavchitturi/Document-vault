"""Search API + a small web page for trying retrieval by hand.

Endpoints:
    GET /                     the search page
    GET /api/search?q=...     top-k passages for a query
    GET /api/search?qid=...   same, for a BEIR test question, plus where its gold passage ranked
    GET /api/examples?n=8     random test questions to try

Usage (after steps 1 and 2 have been run, so the embedding cache exists):
    python -m src.api.app          # then open http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import logging
import random
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from src.retrieval.searcher import VectorSearcher

STATIC_DIR = Path(__file__).parent / "static"
GOLD_SEARCH_DEPTH = 100  # how far down we look for a test question's gold passage

logger = logging.getLogger("api")


def create_app(searcher: VectorSearcher, queries: dict[str, str], qrels: dict[str, dict[str, int]]) -> FastAPI:
    """Build the app around an already-loaded searcher (tests pass a fake one)."""
    app = FastAPI(title="RAG hybrid search")
    test_qids = [q for q in qrels if q in queries]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/examples")
    def examples(n: int = Query(8, ge=1, le=50)) -> list[dict]:
        return [{"qid": q, "question": queries[q]} for q in random.sample(test_qids, min(n, len(test_qids)))]

    @app.get("/api/search")
    def search(
        q: str | None = Query(None, max_length=500),
        qid: str | None = None,
        k: int = Query(10, ge=1, le=50),
    ) -> dict:
        if qid is not None:
            if qid not in queries:
                raise HTTPException(404, f"unknown question id {qid}")
            q = queries[qid]
        if not q or not q.strip():
            raise HTTPException(400, "pass a question as ?q=... or ?qid=...")

        gold_ids = {d for d, s in qrels.get(qid, {}).items() if s > 0} if qid else set()
        # For test questions, search deeper so we can report the gold rank even when it's below k.
        hits, took_ms = searcher.search(q, top_k=max(k, GOLD_SEARCH_DEPTH) if gold_ids else k)
        gold = [
            {"doc_id": g, "rank": next((h.rank for h in hits if h.doc_id == g), None)}
            for g in sorted(gold_ids)
        ]
        return {
            "question": q,
            "qid": qid,
            "method": "vector",
            "took_ms": round(took_ms, 1),
            "gold": gold,  # empty for free-typed questions
            "gold_search_depth": GOLD_SEARCH_DEPTH,
            "results": [asdict(h) | {"is_gold": h.doc_id in gold_ids} for h in hits[:k]],
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="nq")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--processed-dir", default="data/processed")
    parser.add_argument("--split", default="test")
    parser.add_argument("--model", default=None, help="defaults to the step 2 model")
    parser.add_argument("--device", default=None)
    parser.add_argument("--no-title", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    from src.ingestion.loader import load_qrels, load_queries
    from src.retrieval.chunk_store import load_chunks, load_or_embed_chunks
    from src.retrieval.embedder import DEFAULT_MODEL, Embedder

    data_dir = Path(args.processed_dir) / args.dataset
    chunks_path = data_dir / "chunks.jsonl"
    if not chunks_path.exists():
        raise SystemExit(f"{chunks_path} not found: run step 1 first (python -m src.ingestion.run)")
    chunks = load_chunks(chunks_path)
    embedder = Embedder(args.model or DEFAULT_MODEL, device=args.device)
    embeddings = load_or_embed_chunks(chunks, chunks_path, embedder, data_dir / "embeddings", not args.no_title)
    raw_dir = Path(args.raw_dir) / args.dataset
    app = create_app(VectorSearcher(chunks, embeddings, embedder), load_queries(raw_dir), load_qrels(raw_dir, args.split))

    logger.info("Open http://%s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
