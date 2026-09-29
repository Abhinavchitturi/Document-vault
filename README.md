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
| 4 | OCR | ✅ Photos and scanned PDFs: auto-rotated, cleaned up, answers highlighted on the scan, blurry scans flagged |
| 5 | Better retrieval (BM25 + vector) | ✅ Hybrid search: meaning (vector) + exact words and numbers (BM25), fused; exact ID matches first |
| 6 | Authentication | ✅ Password and passkey (Touch ID / Face ID / fingerprint) sign-in, one account per family member, each sees only their own documents; admin adds members and resets passwords |
| 7 | Mobile app | ✅ Flutter app (`mobile/`): chat, documents, scan with the camera; paired by QR code over the home Wi-Fi, HTTPS pinned to the vault's own certificate, Face ID / fingerprint lock |
| 8–13 | Classification, expiry, reminders, security, sharing, deployment | ⏳ |

## Privacy
- For our family only. The repository is private and contains **code and fictional samples only**.
- Real documents, their text and the search index live in `data/` (git-ignored) on your computer.
- **Local models only**: embeddings (Snowflake Arctic Embed), answers (a local LLM via Ollama)
  and OCR (Tesseract) all run on your machine. No models from Chinese developers are used. The LLM client refuses any non-local address, the
  embedding model runs offline once downloaded, and the web page loads nothing from the internet.
- Every family member signs in with their own password, and the server only ever shows them their own
  documents. The app still listens on `127.0.0.1` only: until HTTPS arrives (Phase 13), don't expose it to a network.

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
For photos and scanned PDFs (includes English and page-orientation detection):
```bash
brew install tesseract
```
Scans and phone photos are turned upright (phone rotation and sideways or upside-down scans),
contrast-cleaned and read. Answers are highlighted on the scan itself. A page read with under 70%
confidence is marked **hard to read** in Documents, at upload and in the viewer. Retake those
photos flat, in good light and in focus.
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
Then open http://localhost:8000 (use `localhost`, not `127.0.0.1`: passkeys only work there). One process serves both the pages and the API; Node is only
needed to build the pages.

| Page | What it's for |
|---|---|
| **Sign in** | Name and password. The very first visit is **Set up your vault**: the current owner (you) picks a password and becomes the admin, keeping all documents. |
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
- **Owner**: before sign-in is set up, the vault belongs to your computer's user name (and follows
  it if you rename your Mac user). The setup screen turns that owner into your account.

## Phone app (Phase 7)
Start the vault with phone access, then pair each phone once:
```bash
python -m backend.api.app --phones
```
Mac: **Settings → Phones → Pair a phone** shows a QR code. Phone: open the Document Vault app →
**Scan pairing code**. Phones connect over HTTPS on port 8443 to a certificate made on this Mac;
the app is locked to that exact certificate, and only paired phones are served (no web pages or
password sign-in on the network). Remove a phone in Settings → Phones. While it serves phones,
the vault keeps the Mac from going to sleep (it can still sleep when you close a MacBook's lid).
How to install the app: [mobile/README.md](mobile/README.md).

**Near the Mac, no internet needed**: the phone and Mac on the same Wi-Fi (a router, even with
the internet down, or the phone's hotspot with mobile data off).

**From anywhere (another city or state)**: a private tunnel, [Tailscale](https://tailscale.com)
(free personal plan). It links your own devices with WireGuard encryption; nothing is opened to
the public internet, and Tailscale's servers only introduce the devices, they can't read the traffic.
1. Mac: install Tailscale (App Store or tailscale.com/download) and sign in.
2. Each Android phone: install Tailscale from the Play Store and sign in to the **same** Tailscale
   account (or accept an invite to it), and leave it connected.
3. Restart the vault with `--phones` and pair (or re-pair) the phones: the QR code now includes
   the Mac's Tailscale address. The app tries the home Wi-Fi address first, then the tunnel, and
   remembers whichever answers.

The catch: the Mac at home must be switched on, awake and running the vault at that moment. If it
isn't, the app says it can't reach the vault. Phone uploads are stored on the Mac, in that
person's account (the phone keeps no copy).

## Family accounts (Phase 6)
- **First visit**: *Set up your vault*. Your name is filled in; choose a password (8+ characters).
  You become the **admin**, and your existing documents stay yours.
- **Add family members**: Settings → Family members → *Add a family member*. Tell them the password;
  they can change it in Settings → You. Each person only sees and searches their own documents.
- **Passkeys (Touch ID / Face ID / fingerprint)**: Settings → You → *Add a passkey*, then use
  *Sign in with Touch ID or fingerprint* on the sign-in screen. Open the vault as
  **http://localhost:8000**: browsers don't allow passkeys on `127.0.0.1` (the sign-in screen links
  to the right address). Only the device's public key is stored; your fingerprint never leaves it.
  Mac passkeys sync to your iPhone through iCloud Keychain. On a Mac without Touch ID, Chrome can
  use your phone: choose *Use a phone or tablet* and scan the QR code. Signing in to the vault
  *from* a phone's browser needs the HTTPS address that Phase 13 adds.
- **Forgotten password**: the admin sets a new one (Settings → Family members → *Reset password*).
  That person is signed out everywhere.
- **Everyone locked out** (e.g. the admin forgot theirs): from a terminal on this computer
  ```bash
  python -m backend.auth.cli list
  python -m backend.auth.cli reset-password abhinav
  ```
- **How it's protected**: passwords are stored only as salted scrypt hashes. Sign-in gives the
  browser an HttpOnly, SameSite=Strict cookie that lasts 14 days; the database keeps only its hash.
  Five wrong passwords lock that name for 15 minutes. Requests from other websites are refused.
  Every `/api` call except signing in requires a signed-in person, checked on the server.

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
A fictional family (the Mehras) with 13 documents and 32 questions with known answers (8 of them look up a document by a number, like a serial or policy number):

| Path | What |
|---|---|
| `samples/pdfs/` | The documents as PDFs, to upload (e.g. `insurance/Bike Insurance 2026.pdf`) |
| `samples/documents/` | The same documents as text (the source; edit these) |
| `samples/questions.jsonl` | Questions, the correct document, and the exact answer text |
| `samples/make_pdfs.py` | Regenerates the PDFs after editing a sample |
| `samples/scans/` | OCR tests: an upside-down scanned gas bill (PDF with no text) and a grey, tilted phone photo of a warranty card |
| `samples/make_scans.py` | Regenerates the scans |

```bash
python -m backend.upload.ingest samples/pdfs
```
All names, companies and numbers are made up.

## Evaluation
Compare the three search methods (hybrid, vector only, keyword only):
```bash
python -m backend.eval.evaluate --show-misses
```
Hybrid search plus local LLM answers:
```bash
python -m backend.eval.evaluate --with-llm --show-misses
```
Builds a throwaway vault from the samples (your real vault isn't touched), asks the 32
questions and writes `results/eval_search.json` (or `eval_hybrid_llm.json`).

| Retrieval | Recall@1 | Recall@3 | MRR@5 | Answer@3 | LLM answer accuracy |
|---|---|---|---|---|---|
| Keyword only (BM25) | 0.938 | 1.000 | 0.969 | 1.000 | — |
| Vector only (Arctic Embed M v1.5) | — | — | — | — | — |
| **Hybrid (app default)** | — | — | — | — | — |

Rows marked — are filled in by running the command above (the embedding model runs on your Mac).

**How hybrid search works** (`backend/retriever/`): every passage is ranked twice, by meaning
(the embedding model: "when does my bike cover end" finds "expires on") and by exact words
(BM25: rare words and numbers like `KMI/PC/25/0093318` count most). The two rankings are merged
with Reciprocal Rank Fusion. If the question contains a number copied from a document (policy,
serial, account or phone number), passages with that exact number come first. Start the app
with `--search vector` or `--search keyword` to try one method alone.

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
| `backend/parser/` | Text from PDF / Word / text / photos per page (`extract.py`); OCR with orientation, clean-up, word positions and confidence (`ocr.py`); document types (`categories.py`) |
| `backend/chunker/` | Token-window chunking that never cuts mid-word |
| `backend/embeddings/` | Snowflake Arctic Embed M v1.5; offline mode once downloaded |
| `backend/database/` | PostgreSQL schema (`users`, `documents`, `document_chunks`) and connection |
| `backend/vector_store/` | The vault: records in the database, vectors in FAISS, original files on disk |
| `backend/auth/` | Sign-in: password hashing (`passwords.py`), accounts and sessions (`accounts.py`), passkeys (`passkeys.py`), paired phones (`devices.py`), terminal tool (`cli.py`) |
| `backend/api/lan.py` | The phone listener's certificate and the Mac's Wi-Fi address |
| `mobile/` | The Flutter phone app (see `mobile/README.md`) |
| `backend/retriever/` | Question → passages: hybrid fusion (`retriever.py`), keyword search (`bm25.py`) |
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
