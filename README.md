# RAG Pipeline with Hybrid Search

A production-style Retrieval-Augmented Generation system combining **vector search + BM25
keyword search + cross-encoder reranking**, evaluated on BEIR's Natural Questions subset.

See [PROJECT_BRIEF.md](./PROJECT_BRIEF.md) for the full design, dataset choice, tech stack,
and build order.

## Status
🚧 In progress — step 1 (ingestion + chunking).

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Eval results
_(filled in as each retrieval stage is built — see PROJECT_BRIEF.md build order)_

| Stage | Recall@10 | MRR@10 |
|---|---|---|
| Vector-only | — | — |
| Hybrid (vector + BM25, RRF) | — | — |
| Hybrid + reranked | — | — |
