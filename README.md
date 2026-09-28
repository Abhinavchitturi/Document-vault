# Secure Digital Document Vault

A private, family-only vault for important papers (insurance policies, warranty cards,
property documents, bills) that you can ask questions in plain English. Every model runs on
your own computer: nothing is sent to a cloud AI service.

> **You:** When does my bike insurance expire?
> **Vault:** Your bike insurance expires on 14 March 2027 **[1]**.
> *[1] Bike Insurance 2026.pdf, page 1*

Built in 13 phases, from a core RAG engine to a mobile app with reminders and family sharing.
See **[PROJECT_BRIEF.md](./PROJECT_BRIEF.md)** for the vision, the phases, the architecture
and the privacy decisions.

## Status

| Phase | | State |
|---|---|---|
| 1 | Core RAG engine | ✅ Upload → parse → chunk → embeddings → FAISS → retrieve → local LLM answer with citations |
| 2 | Document metadata | 🟡 Stored in a JSON file; PostgreSQL to come |
| 3 | Web application | 🟡 Single-page app (upload, library, ask); React + Next.js to come |
| 4 | OCR | ✅ Tesseract for photos and scanned PDFs |
| 5 | Better retrieval (BM25 + vector) | 🟡 Vector search and evaluation harness; hybrid to come |
| 6–13 | Auth, mobile, classification, expiry, reminders, security, sharing, deployment | ⏳ |

## Privacy
- For our family only. The repository is private and contains **code and fictional samples only**.
- Real documents, their text and the search index live in `data/` (git-ignored) on your computer.
- **Local models only**: embeddings (Snowflake Arctic Embed), answers (a local LLM via Ollama)
  and OCR (Tesseract) all run on your machine. No models from Chinese developers are used. The LLM client refuses any non-local address, the
  embedding model runs offline once downloaded, and the web page loads nothing from the internet.
- The app listens on `127.0.0.1` only and has no login yet (Phase 6), so don't expose it to a network.

## Setup (once)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Answers: install Ollama from https://ollama.com, open it, then download the model (~5 GB)
ollama pull llama3.1:8b

# Optional: photos and scanned PDFs
brew install tesseract
```
The first run also downloads the Arctic Embed embedding model (~440 MB) from Hugging Face; after
that everything works offline.

## Run
```bash
python -m backend.api.app    # open http://127.0.0.1:8000
```
Drag documents into **Your documents**, then ask a question. Each answer cites its sources;
click a citation number to jump to the passage, and **Open document** opens the file at that page.

- Another local model: `python -m backend.api.app --llm-model gemma2:9b` (after `ollama pull gemma2:9b`)
- Search only, no answers: `--no-llm`. If Ollama isn't running, the page says so and still shows the matching passages.

Import a whole folder instead of uploading one by one. Subfolders named `insurance/`,
`warranty/`, `bill/` or `property/` set the category; anything else is auto-detected:
```bash
python -m backend.upload.ingest ~/Documents/family-papers
```
Deleting `data/index/` resets the vault.

## Try it with dummy documents
A fictional family (the Mehras) with 13 documents and 24 questions with known answers:

| Path | What |
|---|---|
| `samples/pdfs/` | The documents as PDFs, to upload (e.g. `insurance/Bike Insurance 2026.pdf`) |
| `samples/documents/` | The same documents as text (the source; edit these) |
| `samples/questions.jsonl` | Questions, the correct document, and the exact answer text |
| `samples/make_pdfs.py` | Regenerates the PDFs after editing a sample |

```bash
python -m backend.upload.ingest samples/pdfs
```
All names, companies and numbers are made up.

## Evaluation
```bash
python -m backend.eval.evaluate --show-misses              # retrieval only
python -m backend.eval.evaluate --with-llm --show-misses   # + local LLM answers
```
Builds a throwaway vault from the samples (your real vault isn't touched), asks the 24
questions and writes `results/eval_vector.json` (or `eval_vector_llm.json`).

| Retrieval | Recall@1 | Recall@3 | MRR@5 | Answer@3 | LLM answer accuracy |
|---|---|---|---|---|---|
| Vector (Arctic Embed M v1.5) + best local LLM | — | — | — | — | — |
| Hybrid (BM25 + vector), Phase 5 | — | — | — | — | — |

- **Recall@k**: correct document in the top k
- **MRR@5**: how high the correct document ranks (1 = always first)
- **Answer@k**: the exact answer text is inside one of the top-k passages
- **LLM answer accuracy**: the written answer contains the expected text (strict)

## Choosing the models
Models are picked by measuring on the sample questions, not by reputation. No models from
Chinese developers are used.

**Answer LLM.** Shortlist: `gemma3:12b` (bigger) and `llama3.1:8b` (smaller), ~13 GB together:
```bash
ollama pull gemma3:12b && ollama pull llama3.1:8b
python -m backend.eval.compare_llms gemma3:12b llama3.1:8b --show-misses
```
Every model gets the same retrieved passages for the 24 sample questions; the table ranks
them by accuracy, then speed, and `results/llm_comparison.json` keeps every answer. Start
the app with the winner: `python -m backend.api.app --llm-model <winner>`.

**Embedding model.** Default is Snowflake Arctic Embed M v1.5 (bigger); compare with the smaller S:
```bash
python -m backend.eval.evaluate --model Snowflake/snowflake-arctic-embed-m-v1.5
python -m backend.eval.evaluate --model Snowflake/snowflake-arctic-embed-s
```
Switching models re-embeds your vault automatically the next time the app starts.

## Code
Phase 1 layout:

| Folder | Role |
|---|---|
| `backend/upload/` | File → chunks → embeddings → vault; folder import (`ingest.py`) |
| `backend/parser/` | Text from PDF / Word / text / photos per page, OCR (`extract.py`); document types (`categories.py`) |
| `backend/chunker/` | Token-window chunking that never cuts mid-word |
| `backend/embeddings/` | Snowflake Arctic Embed M v1.5; offline mode once downloaded |
| `backend/vector_store/` | Documents, chunks, FAISS index and original files on disk |
| `backend/retriever/` | Question → passages (Phase 5 adds BM25 here) |
| `backend/llm/` | Local Ollama client and the answer-with-citations prompt |
| `backend/api/` | FastAPI app and the web page |
| `backend/eval/` | Scoring on the sample questions; `compare_llms.py` ranks local LLMs |

## Tests
```bash
pytest -q
```
46 tests. They use a fake embedder and a fake LLM, so they run in about two seconds without
downloading models or running Ollama.
