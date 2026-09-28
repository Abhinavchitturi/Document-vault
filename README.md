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
| 2 | Document metadata | ✅ PostgreSQL: `users`, `documents`, `document_chunks`; every document has an owner |
| 3 | Web application | ✅ React + Next.js: Login, Dashboard, Chat, Documents, Upload, Settings |
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
Python packages:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
PostgreSQL, which holds the document records (runs on your Mac, starts automatically at login):
```bash
brew install postgresql@16
brew services start postgresql@16
createdb vault
```
Ollama for answers: install it from https://ollama.com, open it, then download the model (~5 GB):
```bash
ollama pull llama3.1:8b
```
Optional, for photos and scanned PDFs:
```bash
brew install tesseract
```
The web app (built once, and again after pulling changes to `frontend/`):
```bash
brew install node
cd frontend
npm install
npm run build
cd ..
```
The first run also downloads the Arctic Embed embedding model (~440 MB) from Hugging Face; after
that everything works offline.

## Run
```bash
python -m backend.api.app
```
Then open http://127.0.0.1:8000. One process serves both the pages and the API; Node is only
needed to build the pages.

| Page | What it's for |
|---|---|
| **Login** | Continue as the vault's owner. Real sign-in (passwords, one account per family member) is Phase 6; until then this screen is a convenience, not security. |
| **Dashboard** | Document counts by type, recently added, a quick "ask" box, and whether the assistant and OCR are ready. |
| **Chat** | Ask like a chatbot: answers stream in with numbered sources. Click one to see the page with the passage lightly highlighted and the answer itself (amounts, dates, policy numbers) strongly highlighted; **Open original** opens the untouched file. Follow-ups work; the chat is kept while you switch pages and forgotten when you close the tab. |
| **Documents** | Every document, filterable by type and searchable by name. Change a document's type if auto-detect got it wrong, view it, open the original, or remove it. |
| **Upload** | Drag and drop PDFs, Word files, text or photos; choose a type or let it detect one. |
| **Settings** | Switch the answer model (any model you've downloaded in Ollama), theme, and where your data lives. |

The pages load nothing from the internet: the server sends a Content-Security-Policy that
only allows this computer.

Working on the pages: run `python -m backend.api.app` and, in another terminal,
`cd frontend && npm run dev`, then open http://localhost:3000 (it forwards `/api` to :8000).

- Another local model: choose it in **Settings**, or start with `--llm-model gemma3:12b` (after `ollama pull gemma3:12b`)
- If Ollama isn't running, the header shows **Assistant offline**, and replies say how to start it and point to the closest line in your documents.

Import a whole folder instead of uploading one by one. Subfolders named `insurance/`,
`warranty/`, `bill/` or `property/` set the category; anything else is auto-detected:
```bash
python -m backend.upload.ingest ~/Documents/family-papers
```
Where things live:
- **Document records** (name, type, upload date, owner, file path, passage text): the `vault`
  PostgreSQL database. Choose another with `--database-url` or `VAULT_DATABASE_URL`; only
  databases on this computer are accepted. `--database-url sqlite:///data/index/vault.db`
  gives a single-file setup with no server.
- **Search vectors and original files**: `data/index/` (git-ignored).
- **Owner**: until login arrives (Phase 6), a new vault belongs to your computer's user name.
  Change it under **Settings → Your name**; the vault remembers it and your documents stay
  yours. `--owner` or `VAULT_OWNER` pick an owner explicitly.

If the app says it can't connect to the database, it prints PostgreSQL's own reason and the
commands that fix it. The common one after renaming your Mac user is
`role "<new name>" does not exist`: Homebrew's PostgreSQL only has a login for the name you had
when you installed it, so create one for the new name (run once, using your old name):
```bash
psql -U OLDNAME -d postgres -c 'CREATE ROLE "NEWNAME" WITH LOGIN SUPERUSER;'
psql -U OLDNAME -d postgres -c 'ALTER DATABASE vault OWNER TO "NEWNAME";'
```

A vault created before Phase 2 (`documents.json` in `data/index/`) is moved into the database
automatically the first time the app starts. To reset everything: `dropdb vault && createdb vault`
and delete `data/index/`.

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
Retrieval only:
```bash
python -m backend.eval.evaluate --show-misses
```
Retrieval plus local LLM answers:
```bash
python -m backend.eval.evaluate --with-llm --show-misses
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
Layout:

| Folder | Role |
|---|---|
| `backend/upload/` | File → chunks → embeddings → vault; folder import (`ingest.py`) |
| `backend/parser/` | Text from PDF / Word / text / photos per page, OCR (`extract.py`); document types (`categories.py`) |
| `backend/chunker/` | Token-window chunking that never cuts mid-word |
| `backend/embeddings/` | Snowflake Arctic Embed M v1.5; offline mode once downloaded |
| `backend/database/` | PostgreSQL schema (`users`, `documents`, `document_chunks`) and connection |
| `backend/vector_store/` | The vault: records in the database, vectors in FAISS, original files on disk |
| `backend/retriever/` | Question → passages (Phase 5 adds BM25 here) |
| `backend/llm/` | Local Ollama client and the answer-with-citations prompt |
| `backend/viewer/` | Finds the passage and the answer's facts on a page (PDF boxes, Word/text spans) |
| `backend/api/` | FastAPI app: the API, and serving the built web app |
| `frontend/` | Next.js web app (React, TypeScript), built to static files in `frontend/out/` |
| `backend/eval/` | Scoring on the sample questions; `compare_llms.py` ranks local LLMs |

## Tests
```bash
pytest -q
```
76 tests. They use a fake embedder and a fake LLM, so they run in a few seconds without
downloading models or running Ollama. By default they use SQLite; to run them against
PostgreSQL (each test gets a throwaway database):
```bash
VAULT_TEST_DATABASE_URL=postgresql+psycopg://localhost/postgres pytest -q
```
