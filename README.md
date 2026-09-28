# RAG Pipeline with Hybrid Search

A production-style Retrieval-Augmented Generation system combining **vector search + BM25
keyword search + cross-encoder reranking**, evaluated on BEIR's Natural Questions subset.

See [PROJECT_BRIEF.md](./PROJECT_BRIEF.md) for the full design, dataset choice, tech stack,
and build order.

## Status
🚧 In progress: step 1 (ingestion + chunking) done; step 2 (vector-only baseline) in progress.

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

pytest -q
```

## Eval results
_(filled in as each retrieval stage is built — see PROJECT_BRIEF.md build order)_

| Stage | Recall@10 | MRR@10 |
|---|---|---|
| Vector-only | — | — |
| Hybrid (vector + BM25, RRF) | — | — |
| Hybrid + reranked | — | — |
