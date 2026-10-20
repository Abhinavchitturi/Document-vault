# How the Document Vault Works

A complete walkthrough of the application: what it is, every feature, how a document travels through it, how it is secured, and how the code is laid out. Written from the codebase and the project's README / PROJECT_BRIEF.

---

## 1. What it is

A **private, family-only vault** for important papers (insurance policies, warranty cards, bills, property documents, ID cards). You upload a PDF, Word file or photo once; afterwards you ask questions in plain English and get a short answer with numbered sources:

> **You:** When does my bike insurance expire?
> **Vault:** Your bike insurance expires on 14 March 2027 **[1]**.
> *[1] Bike Insurance 2026.pdf, page 1*

Key principles:

- **Everything runs on your own computer.** Embeddings (Snowflake Arctic Embed), answers (a local LLM via Ollama, default `llama3.1:8b`) and OCR (Tesseract) are all local. The LLM client refuses non-local addresses; the embedding model runs offline once downloaded; the web UI loads nothing from the internet (strict Content-Security-Policy).
- **Family-only.** No public sign-up; the admin adds members.
- **Encrypted at rest** with a key in the macOS Keychain.
- **Three ways in:** the web app on the Mac, a Flutter phone app (iOS/Android), and terminal tools.

---

## 2. Architecture at a glance

```
 Browser (Next.js, static export) ──┐
                                    ├──►  FastAPI server (backend/api/app.py)
 Phone app (Flutter, HTTPS :8443) ──┘          │
   paired, certificate-pinned                  ├─ Auth (passwords, passkeys, sessions, paired phones)
                                               ├─ Upload pipeline: parse → OCR → classify → facts → chunk → embed
                                               ├─ Vault store: PostgreSQL (records) + FAISS (vectors) + files on disk
                                               ├─ Retriever: hybrid vector + BM25 with fusion
                                               ├─ LLM (Ollama): answer with citations, streamed
                                               ├─ Reminders, Viewer/highlights, Security, Activity log
                                               └─ One process also serves the built web pages
```

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Records | PostgreSQL via SQLAlchemy Core (SQLite supported for single-file setup and tests) |
| Vector search | FAISS + Snowflake Arctic Embed M v1.5 (sentence-transformers) |
| Keyword search | Custom BM25 |
| Answers | Ollama (`llama3.1:8b` default; any downloaded model can be selected) |
| OCR | Tesseract (English + Hindi), pillow, pillow-heif (HEIC) |
| PDF/Word | pypdfium2, python-docx |
| Key-fact extraction | Needle 3 (cactus-needle) → Ollama → text patterns |
| Auth | scrypt password hashes, WebAuthn passkeys (py_webauthn) |
| Encryption | AES-256-GCM (`cryptography`), key in Keychain (`keyring`) |
| Web UI | Next.js / React / TypeScript, built to static files in `frontend/out/` |
| Phone app | Flutter (Dart) |
| Ops | `./vault` script, macOS launchd, Tailscale for remote access |

---

## 3. Features

### 3.1 Ask your documents (Chat)
- Streaming chat answers (newline-delimited JSON events: `sources`, `token`, `done`, `error`) from `POST /api/chat`.
- Answers use **only** the retrieved numbered passages, cite them as `[n]`, and reply exactly "I'm not able to find that in your documents." when the answer isn't there.
- **Follow-ups** work (last three Q/A pairs are sent as history, and used to improve retrieval). The chat lives while you switch pages and is forgotten when the tab closes.
- **Date-aware:** today's date is in the prompt, so when an expired policy and its renewal both exist the current one wins.
- **ID-card rules:** Aadhaar (12 digits) vs VID (16 digits) vs PAN are distinguished; numbers are copied character by character (e.g. house number `2-11/A`).
- A non-streaming `POST /api/ask` exists for scripts and evaluation.
- Optional category filter restricts search to one document type.

### 3.2 Source viewer with highlights
- Click a citation to see the page image with the **passage lightly highlighted** and the **answer itself strongly highlighted** (amounts, dates, policy numbers).
- Works on PDF (boxes), scans/photos (OCR word positions) and Word/text (spans) — `backend/viewer/highlight.py`.
- "Open original" returns the untouched file.

### 3.3 Upload
- Drag-and-drop PDFs, Word files, text, and photos (including **HEIC** from iPhone/Android). Files are recognised **by content**, not extension.
- Choose a type or leave it on **Detect automatically**.
- **Locked PDFs** (e-Aadhaar, e-PAN, bank statements): upload asks for the password; the vault stores an unlocked copy and never stores the password.
- Folder import from the terminal: `python -m backend.upload.ingest <folder>` (subfolder names like `insurance/` set the category).

### 3.4 OCR for scans and photos
- Pages are **auto-rotated** (phone orientation, sideways/upside-down), contrast-cleaned, read twice (as-is and with shadows/glare evened out), unclear ones re-read larger and English-only; the fullest, surest reading wins.
- English + Hindi (PAN and Aadhaar cards are half Hindi).
- Pages under **70% confidence** are flagged **hard to read** (the score counts only real words, so logos/photos/signatures don't drag it down).
- PDFs that are really pictures are OCR'd too.
- Diagnose one file safely (prints no text): `python -m backend.parser.diagnose <file>`.

### 3.5 Automatic classification
Types: `insurance`, `warranty`, `bill`, `property`, `identity` (PAN, Aadhaar, passport, driving licence, voter ID), `other`.

Three steps, cheapest first (`backend/classify/`):
1. **Keywords** ("premium", "sum insured", "due date", "serial number"…) + file name.
2. **Meaning:** embedding similarity to a description of each type.
3. **Answer model** asked only when 1 and 2 disagree or are unsure.

Each document remembers whether a person or the vault chose its type and the confidence. Under 60% shows **check type**; picking a type manually settles it permanently. Re-sort old uploads with `python -m backend.classify.run [--apply]`.

### 3.6 Key facts: expiry, due date, amount, reference, issuer
Read at upload (`backend/extract/facts.py`), best reader first:
1. **Needle 3** (35 MB, offline after one-time setup: `python -m backend.extract.needle_setup`)
2. The **Ollama answer model**
3. **Text patterns** (always the fallback and used to fill gaps)

Safety checks: any date, amount or number that doesn't appear in the document is dropped; bills never get an "expiry". Users can correct facts (pencil icon) and the vault never overwrites corrections. ID numbers (PAN/Aadhaar/passport/licence) become the document's reference; a passport's or licence's expiry feeds reminders. **Aadhaar numbers display masked** (`XXXX XXXX 1234`) but are searchable in full. Back-fill older documents with `python -m backend.extract.run [--apply]`.

### 3.7 Dashboard
Document counts by type, recently added, quick "ask" box, assistant/OCR readiness, **Coming up** (next 90 days plus anything missed in the last 30) and the **Reminders** card with **Paid / Done** (and Undo).

### 3.8 Documents page
All documents, filter by type, search by name; change type, edit details, view pages, open original, delete, make private, share. Shows "check type" and "hard to read" badges.

### 3.9 Reminders
Defaults: 30, 7, 1 and 0 days before a policy/warranty expires; 7, 1 and 0 days before a bill is due. Each person chooses their own days and time (Settings → Reminders).

| Channel | How |
|---|---|
| Phone | Scheduled as ordinary local notifications each time the app reaches the vault — fire even if the app is closed and the Mac is off |
| Mac | Desktop notifications while the vault runs (also catches ones missed in the last 3 days); admin on by default |
| Calendar | Downloadable `.ics` with alarms for Apple/Google/Outlook |
| Dashboard | Reminders card with Paid/Done |

Nothing is sent by email or any online service.

### 3.10 Accounts and sign-in
- First visit: **Set up your vault** — the current owner picks a password and becomes **admin**, keeping existing documents.
- Admin adds family members and resets passwords (reset signs that person out everywhere and unpairs their phones).
- **Passkeys** (Touch ID / Face ID / fingerprint) via WebAuthn; only public keys are stored. Use `http://localhost:8000` (browsers block passkeys on `127.0.0.1`).
- Passwords: salted **scrypt** hashes. Sessions: random token in an **HttpOnly, SameSite=Strict** cookie for 14 days; only its SHA-256 is stored. **5 wrong passwords lock the name for 15 minutes.** Cross-site requests are refused; every `/api` call except sign-in requires a signed-in user, checked server-side.
- Lockout recovery from the terminal: `python -m backend.auth.cli list | reset-password <name>`.

### 3.11 Family sharing
- **Whole-family mode (default):** everyone sees, searches and asks about every document not marked private (labelled "Dad's", "Mom's"…).
- **Each-their-own mode** (admin switch): documents private to the owner, who shares individual documents with chosen members; recipients can "Remove from my list".
- Only the **owner** changes or deletes a document; reminders go to the owner (and direct share recipients). Mode switches, privacy and sharing changes are all logged.

### 3.12 Phone app (Flutter)
Tabs: **Chat**, **Documents**, **Scan**, **Settings**.
- **Pairing:** Settings → Phones → *Pair a phone* shows a one-time QR (valid 10 minutes). It carries the vault's addresses, the SHA-256 fingerprint of the vault's own certificate, and a one-time code. The phone swaps the code for its own long-lived revocable key, kept in iOS Keychain / Android Keystore.
- **HTTPS on port 8443, certificate-pinned:** the app trusts only that exact certificate (no certificate authorities). Only paired phones are served; no web pages or password sign-in on the network.
- **Addresses:** tries home Wi-Fi first, then Tailscale (private WireGuard tunnel) so it works from anywhere while the Mac is on; refreshes the list each connection.
- **Scan:** photograph a document or pick a photo/PDF/Word file. If the Mac is off, uploads queue on the phone and send automatically (checked on open and every 45 s).
- **Share → Document Vault** from WhatsApp, Files, Gmail, Photos.
- **Lock:** Face ID / fingerprint on open and after 2 minutes in background.
- **Phone reminders** scheduled locally.

### 3.13 Security
- **Encrypted at rest (AES-256-GCM):** original files, OCR results, the search index, and in the database file names, passage text and ID/policy numbers. Files start with the `DVE1` magic; text fields use the `dv1:` prefix.
- **Key in the macOS Keychain** (never in the vault folder). **Recovery key:** 13 groups of 4 characters to write on paper (`python -m backend.security.cli recovery-key | status | restore-key`).
- **Encrypted backups:** one `.dvbackup` file (documents, accounts, passkeys, phones, reminders) locked by a 10+ character password; restorable on a brand-new Mac. From Settings → Security (admin) or `cli backup / restore`.
- **Activity log:** sign-ins (and failures), documents added/opened/changed/removed, phones paired/removed, backups. Kept for a year; document names are encrypted. Users see their own, admin sees all. Searches and questions are logged as "searched"/"asked" with the documents they led to — never the words typed.
- A pre-Phase-11 vault is encrypted in place on first start of the new version.

### 3.14 Search
`GET /api/search` and the chat retriever use **hybrid search**:
- **Vector** (meaning: "when does my bike cover end" finds "expires on")
- **BM25** (exact words and numbers like `KMI/PC/25/0093318`)
- Merged with **Reciprocal Rank Fusion** (k=60).
- If the question contains a number copied from a document (policy, serial, account, phone), passages with that **exact number rank first**.
- Modes can be forced with `--search vector|keyword`.

### 3.15 Settings (web)
Switch answer model (any model downloaded in Ollama), theme (light/dark), your account and password, passkeys, family members (admin), sharing mode, reminders, phones, security/backup, activity, and an About panel (which fact reader is active, where data lives).

### 3.16 Deployment and operations (`./vault`)
| Command | Does |
|---|---|
| `./vault start` | Checks/starts PostgreSQL, Ollama, Tailscale; runs the server in the background under launchd with phone access on; opens the browser; auto-restarts on crash |
| `./vault stop` | Stops it |
| `./vault status` | Running state, plus PostgreSQL, Ollama, Tailscale, pages, OCR readiness |
| `./vault logs [-f]` | Tail `data/logs/vault.log` (person + device on every line) |
| `./vault update` | `git pull`, reinstall/rebuild only what changed, restart, say whether phones need a new app |
| `./vault autostart on/off` | Start at login |

Double-click `Start Vault.command` / `Stop Vault.command` in Finder. The Mac is kept awake while the vault runs.

---

## 4. Life of a document (upload pipeline)

1. **Receive** — file arrives from web, phone, share sheet or CLI.
2. **Recognise** — type detected from content (PDF / DOCX / text / image incl. HEIC); locked PDFs get unlocked with the supplied password.
3. **Extract text per page** — direct text for PDFs/Word/text; OCR for photos, scans and picture-only PDFs (rotation, cleaning, dual reads, confidence).
4. **Classify** — keywords → meaning → LLM tiebreak; confidence stored.
5. **Extract key facts** — expiry, due date, amount, reference, issuer; validated against the text.
6. **Chunk** — token windows (≤510 tokens for the embedding model) with boundaries snapped so words are never cut.
7. **Embed** — Snowflake Arctic Embed M v1.5, locally.
8. **Store** — record + chunk text in PostgreSQL (sensitive fields encrypted), vectors in FAISS (chunk id = DB id), original file encrypted on disk under `data/index/`.
9. **Log** — "document added" in the activity log; reminders recomputed.

## 5. Life of a question

1. Question (+ recent history) arrives at `/api/chat`; access limited to documents the user may see.
2. Retriever runs vector and BM25 searches, fuses them with RRF, boosts exact-number matches.
3. Top passages are numbered and sent with the system prompt (today's date, strict "only these passages" rules) to the local Ollama model.
4. Tokens stream back to the browser/phone; sources arrive first so citations are clickable immediately.
5. `finalize()` extracts `[n]` citations and the "found/not found" flag; the viewer highlights passage and answer on the page.

---

## 6. Data model (PostgreSQL)

| Table | Purpose |
|---|---|
| `users` | Family members; hashed password; `is_admin` |
| `sessions` | SHA-256 of cookie token, expiry |
| `passkeys` | Credential id, public key, sign count |
| `devices` | Paired phones: SHA-256 of each phone's key, last seen |
| `documents` | One row per file: name (encrypted), type, owner, path, facts, privacy flag, classification source/confidence |
| `document_chunks` | Searchable passages; `id` doubles as FAISS vector id |
| `document_shares` | Direct shares (each-their-own mode) |
| `reminder_settings` / `reminder_done` / `reminders_sent` | Per-person reminder choices, "Paid/Done" marks, Mac notifications already shown |
| `vault_meta` | Key/value, e.g. which embedding model built the vectors (switching models re-embeds on next start) |

Where files live: `data/` (git-ignored) — `index/` (FAISS + originals), `tls/` (phone certificate), `logs/`, `launchd/`, `eval_index/`.

---

## 7. API surface (FastAPI, `backend/api/app.py`)

| Area | Endpoints |
|---|---|
| Auth | `GET /api/auth/status`, `POST /api/auth/setup`, `/login`, `/logout`, `/password`, `/passkey/options`, `/passkey` |
| Passkeys | `GET/POST /api/passkeys`, `/api/passkeys/options`, `DELETE /api/passkeys/{id}` |
| Users | `GET/POST /api/users`, `POST /api/users/{id}/password` |
| Phones | `GET /api/mobile/hello`, `POST /api/mobile/pairing`, `GET /api/mobile/addresses`, `POST /api/mobile/pair`, `GET /api/devices`, `DELETE /api/devices/{id}` |
| Documents | `GET/POST /api/documents`, `PATCH/DELETE /api/documents/{id}`, `GET …/file`, `GET …/pages/{n}/image`, `POST …/pages/{n}/highlights` |
| Sharing | `GET /api/family`, `PUT …/shares`, `PUT …/private`, `DELETE …/shares/me` |
| Reminders | `GET /api/upcoming`, `/api/reminders`, `POST /api/reminders/done`, `/settings`, `GET /schedule`, `/calendar.ics` |
| Q&A | `GET /api/search`, `POST /api/chat` (streaming), `POST /api/ask` |
| Admin/other | `GET /api/activity`, `POST /api/backup`, `GET /api/info`, `GET/POST /api/settings` |

The server runs in two modes: **web** (localhost:8000, pages + API) and **phone** (HTTPS :8443, API only, paired devices only).

---

## 8. Repository map

| Path | Role |
|---|---|
| `backend/api/` | FastAPI app (`app.py`), phone listener cert + LAN addresses (`lan.py`) |
| `backend/auth/` | Passwords, accounts/sessions, passkeys, paired devices, CLI |
| `backend/upload/` | Ingest pipeline and folder import |
| `backend/parser/` | Text extraction, OCR, categories, diagnose tool |
| `backend/chunker/` | Token-window chunking |
| `backend/embeddings/` | Arctic Embed wrapper (offline once downloaded) |
| `backend/vector_store/` | The vault: DB records + FAISS + files on disk |
| `backend/retriever/` | Hybrid retrieval (`retriever.py`), BM25 (`bm25.py`) |
| `backend/llm/` | Ollama client, answer/citation prompt, streaming |
| `backend/classify/` | Type classifier and re-sort tool |
| `backend/extract/` | Key-fact readers, back-fill, Needle setup |
| `backend/reminders/` | Reminder rules, phone schedule, `.ics`, Mac notifications |
| `backend/viewer/` | Passage/answer highlighting |
| `backend/security/` | Crypto, Keychain keys, backups, activity log, CLI |
| `backend/database/` | SQLAlchemy schema and connection |
| `backend/deploy/` | `./vault` start/stop/status/logs/update/autostart |
| `backend/eval/` | Retrieval, classification, fact and LLM-comparison evaluations |
| `frontend/` | Next.js app: pages `login`, `/` (dashboard), `chat`, `documents`, `upload`, `settings`; shared components and `lib/api.ts` |
| `mobile/` | Flutter app (screens: lock, pair, home, chat, documents, scan, settings, source) with upload queue, share inbox, certificate pinning, phone reminders |
| `samples/` | Fictional "Mehra" family: 13 documents, 32 questions with known answers, scans and ID-card generators |
| `tests/` | ~150+ pytest tests using a fake embedder and fake LLM (fast, no models needed) |
| `results/` | Evaluation outputs |
| `vault`, `*.command` | Operations entry points |

---

## 9. Setup and development quick reference

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
brew install postgresql@16 && brew services start postgresql@16 && createdb vault
ollama pull llama3.1:8b
brew install tesseract tesseract-lang
brew install node && (cd frontend && npm install && npm run build)
./vault start                      # run everything
pytest -q                          # tests
python -m backend.upload.ingest samples/pdfs     # load sample documents
python -m backend.eval.evaluate --show-misses    # compare hybrid / vector / keyword
```

Frontend development: run `python -m backend.api.app` and `cd frontend && npm run dev` (port 3000 proxies `/api` to 8000). Phone app: see `mobile/README.md` (Flutter; iPhone needs Xcode, free Apple ID reinstall every 7 days).

---

## 10. Evaluation

- **Retrieval:** Recall@1/3, MRR@5, Answer@3 on 32 sample questions (8 are lookups by a number), comparing hybrid, vector-only and keyword-only. Throwaway vault; real data untouched.
- **LLM comparison:** `compare_llms.py` ranks local models (e.g. `gemma3:12b` vs `llama3.1:8b`) by accuracy then speed on the same retrieved passages.
- **Classifier and fact readers:** `classify_eval.py`, `facts_eval.py`.
- **Policy:** models are chosen by measurement; no models from Chinese developers are used.

---

## 11. Known limits / things to remember

- The Mac must be on, awake and running the vault for phones to reach it (reminders already on the phone still fire).
- Phone reminders refresh only when the app connects; open it after adding documents.
- Passkeys need `localhost`, not `127.0.0.1`; sign-in from a phone's *browser* is not supported — phones use the paired app.
- Losing both the Keychain and the recovery key makes the encrypted data unrecoverable — keep the paper recovery key safe.
- With a free Apple ID the iOS app must be reinstalled every 7 days.
