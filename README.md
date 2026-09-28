# Secure Digital Document Vault

A private, family-only vault for important papers (insurance policies, warranty cards,
property documents, bills) that you can ask questions in plain English.

> **You:** When does my bike insurance expire?
> **Vault:** *Bike Insurance 2026.pdf, page 1:* "From 00:00 hrs on 15 March 2026 to midnight of **14 March 2027**."

Built in 13 phases, from a core RAG engine to a mobile app with reminders and family sharing.
See **[PROJECT_BRIEF.md](./PROJECT_BRIEF.md)** for the vision, the phases, the architecture
and the privacy decisions.

## Status

| Phase | | State |
|---|---|---|
| 1 | Core RAG engine | 🟡 Upload, parsing, chunking, BGE embeddings and retrieval work. Next: `backend/` layout, FAISS, LLM answers |
| 2 | Document metadata | 🟡 Stored in a JSON file; PostgreSQL to come |
| 3 | Web application | 🟡 Single-page prototype; React + Next.js to come |
| 4 | OCR | ✅ Tesseract for photos and scanned PDFs |
| 5 | Better retrieval (BM25 + vector) | 🟡 Vector search and evaluation harness; hybrid to come |
| 6–13 | Auth, mobile, classification, expiry, reminders, security, sharing, deployment | ⏳ |

## Privacy
- For our family only. The repository is private and contains **code and fictional samples only**.
- Real documents, their text and the search index live in `data/` (git-ignored) and never leave
  your machine, except the few passages sent to the LLM once answers are added. A local LLM
  option is planned so even that can stay on the machine.
- The app listens on `127.0.0.1` only and has no login yet (Phase 6), so don't expose it to a network.

## Run the prototype
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install tesseract   # optional: needed only for photos and scanned PDFs

python -m src.api.app    # open http://127.0.0.1:8000
```
Drag documents into **Your documents**. To try it without real papers, upload the dummy PDFs
from `samples/pdfs/` (e.g. `samples/pdfs/insurance/Bike Insurance 2026.pdf`), or import them all:
```bash
python -m src.ingestion.ingest samples/pdfs
```
Subfolders named `insurance/`, `warranty/`, `bill/` or `property/` set the category; anything
else is auto-detected. Deleting `data/index/` resets the vault.

## Sample documents
A fictional family (the Mehras) with 13 documents and 24 questions with known answers:

| Path | What |
|---|---|
| `samples/pdfs/` | The documents as PDFs, to upload in the app |
| `samples/documents/` | The same documents as text (the source; edit these) |
| `samples/questions.jsonl` | Questions, the correct document, and the exact answer text |
| `samples/make_pdfs.py` | Regenerates the PDFs after editing a sample |

All names, companies and numbers are made up.

## Evaluation
```bash
python -m src.eval.evaluate --show-misses
```
Builds a throwaway index from the samples (your real vault isn't touched), asks the 24
questions and writes `results/eval_vector.json`.

| Retrieval | Recall@1 | Recall@3 | MRR@5 | Answer@1 | Answer@3 |
|---|---|---|---|---|---|
| Vector (BGE Small v1.5) | — | — | — | — | — |
| Hybrid (BM25 + vector), Phase 5 | — | — | — | — | — |

- **Recall@k**: correct document in the top k
- **MRR@5**: how high the correct document ranks (1 = always first)
- **Answer@k**: the exact answer text is inside one of the top-k passages

## Code
| Module | Role |
|---|---|
| `src/ingestion/extract.py` | Text from PDF / Word / text / photos, per page; OCR for scans |
| `src/ingestion/categories.py` | Document types and keyword-based detection |
| `src/ingestion/chunker.py` | Token-window chunking that never cuts mid-word |
| `src/ingestion/ingest.py` | File → chunks → embeddings → vault; folder import |
| `src/retrieval/embedder.py` | BGE Small v1.5 embeddings |
| `src/retrieval/document_store.py` | Documents, chunks, vectors and files on disk; search |
| `src/api/` | FastAPI app and the web page |
| `src/eval/` | Scoring on the sample questions |

This layout moves to the `backend/` structure described in Phase 1.

## Tests
```bash
pytest -q
```
32 tests; they use a fake embedder, so they run in about a second without downloading models.
