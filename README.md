# Family Documents Assistant

Ask questions about your family's paperwork (insurance policies, warranty cards, bills,
property documents) and get the passage and page that answers it. Everything stays on your
computer.

> "When does my bike insurance expire?" → **bike_insurance_2026.pdf, page 1**: *"From 15 March 2026 to midnight of 14 March 2027"*

Under the hood it's a retrieval-augmented generation (RAG) pipeline, built step by step with a
measured score at each stage. See [PROJECT_BRIEF.md](./PROJECT_BRIEF.md) for requirements,
architecture and build order.

## Status
🚧 Steps 1–2 done: document ingestion (PDF, Word, text, photos with OCR) and vector search
with a web app. Next: hybrid BM25 + vector search.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install tesseract   # optional: needed only for photos and scanned PDFs
```

## Use it
```bash
python -m src.api.app
# open http://127.0.0.1:8000, then drag your documents into "Your documents"
```

Or import a whole folder from the command line. Files in subfolders named `insurance/`,
`warranty/`, `bill/` or `property/` get that category; others are auto-detected:
```bash
python -m src.ingestion.ingest ~/Documents/family-papers
```

To try it without your own documents, import the fictional samples:
```bash
python -m src.ingestion.ingest samples/documents
```

Your library lives in `data/index/` (git-ignored). Deleting that folder resets it.

## How it works
```
upload ─> text per page (OCR if scanned) ─> 300-token chunks ─> bge-small embeddings ─> data/index/
question ─> embed ─> cosine search (optionally within one category) ─> best passage per document
```

| Module | Role |
|---|---|
| `src/ingestion/extract.py` | Text from PDF / Word / text / images, one entry per page; OCR for scans |
| `src/ingestion/categories.py` | Categories and keyword-based auto-detection |
| `src/ingestion/chunker.py` | Token-window chunking that never cuts mid-word |
| `src/ingestion/ingest.py` | File → chunks → embeddings → library; folder import CLI |
| `src/retrieval/embedder.py` | bge-small query/passage embeddings |
| `src/retrieval/document_store.py` | The library on disk: documents, chunks, vectors, original files; search |
| `src/api/app.py`, `src/api/static/index.html` | Web API and page |
| `src/eval/` | Scoring on the sample question set |

## Evaluation
`samples/` holds 13 fictional documents and 24 questions with known answers, including
look-alikes (two years of bike policy, four appliance warranties, three bills).
```bash
python -m src.eval.evaluate --show-misses
```
Builds a throwaway library from the samples (your real library isn't touched) and writes
`results/eval_vector.json`.

| Stage | Recall@1 | Recall@3 | MRR@5 | Answer@1 | Answer@3 |
|---|---|---|---|---|---|
| Vector (bge-small-en-v1.5) | — | — | — | — | — |
| Hybrid (vector + BM25, RRF) | — | — | — | — | — |
| Hybrid + reranked | — | — | — | — | — |

- **Recall@k**: correct document in the top k results
- **MRR@5**: how high the correct document ranks (1 = always first)
- **Answer@k**: the exact answer text is inside one of the top-k passages

## Tests
```bash
pytest -q
```
Tests use a fake embedder, so they run in about a second without downloading models.
