# RAG Pipeline with Hybrid Search

A production-style Retrieval-Augmented Generation system combining **vector search + BM25
keyword search + cross-encoder reranking**, evaluated on BEIR's Natural Questions subset.

See [PROJECT_BRIEF.md](./PROJECT_BRIEF.md) for the full design, dataset choice, tech stack,
and build order.

## Status
🚧 In progress: step 1 (ingestion + chunking) done; step 2 (vector-only baseline) done on the 100k subset.

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Step 1: download BEIR NQ, take a 100k-passage subset (all gold passages + random
# distractors), chunk into 400-token windows with 80-token overlap, print samples
python -m src.ingestion.run --show-samples 5
# -> data/processed/nq/chunks.jsonl, data/processed/nq/chunk_stats.json

# Step 2: embed chunks with bge-small, search with every test question, score against qrels
python -m src.eval.evaluate --show-examples 5
# -> results/step2_vector.json (first run also caches embeddings in data/processed/nq/embeddings/)

# Try it in the browser: search box + real NQ test questions with the correct passage highlighted
python -m src.api.app
# -> open http://127.0.0.1:8000  (needs the step 2 embedding cache; startup takes ~30s)

pytest -q
```

## Eval results
_(filled in as each retrieval stage is built — see PROJECT_BRIEF.md build order)_

BEIR NQ test set (3,452 questions), searched over a 100k-passage subset: every gold passage
plus ~96k random distractors. The subset is much easier than the full 2.68M-passage corpus, so
these numbers are **not** comparable to published BEIR results; compare rows with each other.

| Stage | Recall@1 | Recall@10 | Recall@100 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|
| Vector-only (bge-small-en-v1.5) | 0.679 | 0.950 | 0.989 | 0.836 | 0.857 |
| Hybrid (vector + BM25, RRF) | — | — | — | — | — |
| Hybrid + reranked | — | — | — | — | — |
