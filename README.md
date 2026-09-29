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
| 8 | Classification | ✅ Uploads sorted into insurance / warranty / bill / property / other by keywords + meaning, asking the local answer model only when unsure; unsure ones flagged "check type" |
| 9 | Expiry and key facts | ✅ Expiry, due date, amount, reference number and issuer read from each document (Needle 3 when installed, else the answer model, else text patterns), checked against the text; "Coming up" on the dashboard |
| 10 | Reminders | ✅ Before expiries and due dates: phone notifications (even with the Mac off), Mac notifications, a calendar file, and the Dashboard; days, time and "Paid / Done" per person |
| 11 | Security | ✅ Documents, scans, search index, file names and ID numbers encrypted on disk (AES-256), key in the macOS Keychain with a paper recovery key; encrypted one-file backups; an activity log of sign-ins and document access |
| 12 | Family sharing | ✅ Whole-family mode (default): everyone sees every document not marked private; or each person's own plus documents shared with chosen members. Only the owner changes or deletes a document; every change in the activity log |
| 13 | Deployment | ✅ `./vault start / stop / status / logs / update`, Start/Stop Vault in Finder: checks and starts PostgreSQL, Ollama and Tailscale, runs in the background under macOS launchd (restarted after a crash, logs in `data/logs/`), optional start at login; `update` pulls, installs what changed and says whether phones need a new app |

## Privacy
- For our family only. The repository is private and contains **code and fictional samples only**.
- Real documents, their text and the search index live in `data/` (git-ignored) on your computer.
- **Local models only**: embeddings (Snowflake Arctic Embed), answers (a local LLM via Ollama)
  and OCR (Tesseract) all run on your machine. No models from Chinese developers are used. The LLM client refuses any non-local address, the
  embedding model runs offline once downloaded, and the web page loads nothing from the internet.
- Every family member signs in with their own password, and the server only ever shows them their own
  documents. The app still listens on `127.0.0.1` only: until HTTPS arrives (Phase 13), don't expose it to a network.

## Sharing with family (Phase 12)
**Whole family (the default):** everyone sees, searches and asks about every document anyone
uploaded, marked "Dad's", "Mom's"... Keep one to yourself with the people icon → **Only me (private)**.
Only a document's owner can change its details, make it private or delete it, and reminders go to
the owner (and anyone it was shared with directly), so nobody is pinged about someone else's bills.
"Coming up" on the dashboard shows the whole family's dates.

**Each their own:** the admin can switch this in Settings → Family members. Then documents are
private to whoever added them, and the owner shares single documents with chosen people (people
icon → tick names); those can "Remove from my list".

Switching modes, making a document private and sharing are all recorded in Settings → Activity.

## Security (Phase 11)
**Encrypted on disk.** Everything the vault keeps about your documents is encrypted with AES-256-GCM:
the files themselves, OCR results of scans, the search index, and in the database the file names,
passage text and ID / policy numbers (PAN, Aadhaar...). A copy of `data/` (a backup disk, a synced
folder, a lost laptop's drive) is unreadable without the key. A vault from before Phase 11 is
encrypted in place the first time you start the new version.

**The key** lives in the macOS **Keychain** (Keychain Access → search "Document Vault"), never in the
vault folder. Write down the **recovery key** once and keep it with your important papers:
```bash
cd ~/"Document Secure"
python -m backend.security.cli recovery-key    # 13 groups of 4 letters/digits: write them on paper
python -m backend.security.cli status          # encrypted? where is the key?
python -m backend.security.cli restore-key     # new Mac / Keychain lost: type the recovery key
```

**Encrypted backups**: one `.dvbackup` file with everyone's documents, accounts, passkeys, phones and
reminders, locked with a backup password (10+ characters). Safe on a USB drive or in the cloud.
From Settings → Security (admin), or:
```bash
python -m backend.security.cli backup ~/Desktop
python -m backend.security.cli restore ~/Desktop/"Document Vault backup 2026-09-29 21.00.dvbackup"
```
Restoring works on a Mac that has never seen the vault (the backup carries the key, wrapped by the
backup password). Afterwards everyone signs in again; paired phones keep working.

**Activity log**: sign-ins (and failed ones), documents added, opened, changed and removed, phones
paired and removed, backups. Settings → Activity shows your own; the admin can see everyone's.
Kept for a year; document names in it are encrypted too. From the terminal:
`python -m backend.security.cli activity`.

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
brew install tesseract-lang
```
The second line adds Hindi (and other Indian languages). PAN and Aadhaar cards are half Hindi:
without it those lines turn into gibberish and the card is wrongly marked "hard to read".
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

## Run on the Mac (Phase 13)
From the vault folder in Terminal:
```bash
./vault start
```
It checks the database, Ollama (answers), Tailscale (phones away from home) and the web pages,
starts what's missing, runs the vault in the background with phone access on, and opens it in
the browser. Closing Terminal doesn't stop it; it restarts by itself if it ever crashes.
Or double-click **Start Vault.command** / **Stop Vault.command** in Finder.

| Command | What it does |
|---|---|
| `./vault start` | Start (or restart) the vault in the background |
| `./vault stop` | Stop it; phones show "can't reach the vault" until the next start |
| `./vault status` | Running or not, and whether PostgreSQL, Ollama, Tailscale, pages and OCR are ready |
| `./vault logs` | The end of `data/logs/vault.log` (`./vault logs -f` to follow): who did what, from which device, and every error |
| `./vault update` | `git pull`, install new packages / rebuild pages only if they changed, restart; says whether phones need a new app |
| `./vault autostart on` | Start the vault whenever you log in to the Mac (`off`: only when you start it) |

`data/logs/vault.log` names the person and device on every line, for example
`vault.activity: sunita (Sunita's Redmi) opened a document: House Insurance.pdf [from 100.87.3.12]` and
`vault.requests: sunita (Sunita's Redmi) GET /api/documents -> 200 (12 ms)`. Searches and questions are
logged as "searched" / "asked the assistant" with the documents they led to, never the words typed
(they can contain Aadhaar or PAN numbers). It's plain text, so it does show file names.

The Mac is kept awake while the vault runs. By hand, in the foreground (for troubleshooting):
```bash
python -m backend.api.app --phones
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

## ID cards and locked PDFs
- **Identity** is a document type of its own: PAN, Aadhaar, passport, driving licence, voter ID.
  Their number (PAN, Aadhaar, passport or licence number) is read as the document's reference, and
  a passport's or licence's expiry goes into reminders.
- **Aadhaar numbers are shown masked** on screen (XXXX XXXX 1234), on the web and the phone; the
  full number is only in a document's details (the pencil) and still finds the document in search.
- **Photos and scans** are read in English and Hindi (after `brew install tesseract-lang`). The
  "hard to read" score only counts real words, so a card's logo, emblem, photo and signature no
  longer drag it down; a small, unclear photo is read once more at a larger size.
- **PDFs that are pictures** (a card pasted into a PDF, a scanned page with a little typed text) are
  read with OCR too.
- **Locked PDFs** (e-Aadhaar, e-PAN, bank statements): the upload asks for the password (e-Aadhaar:
  first 4 letters of the name in capitals + year of birth; e-PAN: date of birth as DDMMYYYY). The
  vault keeps an unlocked copy, so it can show and search the document later without asking again.
  The password itself isn't stored.
- Try it with the fictional cards in `samples/scans/identity/` (the locked one's password is
  `ARJU1988`); regenerate them with `python samples/make_id_cards.py`.

## Sorting documents (Phase 8)
Uploads left on **Detect automatically** are sorted by the vault, with the models already on
this computer (no extra download):
1. **Keywords**: "premium", "sum insured", "due date", "serial number"… and the file name.
2. **Meaning**: the search model compares the document's opening with a description of each type.
3. **The answer model** (Ollama) is asked only when 1 and 2 disagree or aren't sure.

Each document remembers whether a person or the vault chose its type, and how sure the vault
was. Under 60% sure, Documents shows **check type** (also on the phone); picking a type in
the menu settles it for good. Documents uploaded before Phase 8 can be sorted with:
```bash
python -m backend.classify.run
python -m backend.classify.run --apply
```
The first only shows the changes; `--apply` makes them. Types a person chose are never
changed. Measure the sorter on the samples, with file names hidden so only the content
counts: `python -m backend.eval.classify_eval --with-llm`.

Why no new model: sorting into five types is easy work for the two models already installed.
Granite or NuExtract would each be another 2–3 GB download for little gain, and Needle 3 is
new and needs its own runtime. They're worth a look for reading dates and amounts (Phase 9)
only if the answer model proves too slow or inaccurate there.

## Expiry dates and key facts (Phase 9)
Each document's **expiry** (policy end, warranty until), **payment due date**, **amount**,
**reference number** and **issuer** are read when it's uploaded. They show under the file
name in Documents and in **Coming up** on the Dashboard (the next 90 days, plus anything
missed in the last 30). Click the pencil on a document to correct them; the vault never
overwrites your corrections.

Who reads them, best first:
1. **Needle 3** (Cactus Compute, Apache-2.0, 35 MB) if installed. Set it up once (this is the
   only step that downloads anything), after which it runs with the internet off:
   ```bash
   pip install cactus-needle
   python -m backend.extract.needle_setup
   ```
   Needle's anonymous usage counts are switched off by the vault.
2. The **answer model** in Ollama, if Needle isn't set up.
3. **Text patterns** ("Due date: 18 September 2026"), always there as the fallback, and used
   to fill any field the model missed.

Whatever reads them, a date, amount or number that doesn't appear in the document is
dropped, and bills never get an "expiry" (a billing period isn't one). Fill in facts for
documents uploaded earlier with `python -m backend.extract.run` (preview), then `--apply`.
Compare the readers on the samples: `python -m backend.eval.facts_eval` (or `--pdfs`).
Settings → About shows which reader is in use.

## Reminders (Phase 10)
Built on the dates from Phase 9. By default: 30, 7 and 1 day before a policy or warranty
expires and on the day; 7 and 1 day before a bill is due and on the day. Each person picks
their own days and time in **Settings → Reminders**.

| Where | How |
|---|---|
| **Phone** | Each time the app reaches the vault it schedules the upcoming reminders as ordinary phone notifications, so they appear on the day **even when the app is closed and the Mac is off**. Open the app now and then (at least after adding documents) to refresh them. |
| **This Mac** | Desktop notifications while the vault is running, including ones missed in the last 3 days while it was off. On for the admin, off for others (Settings → Reminders → Show on this Mac). |
| **Calendar** | Settings → Reminders → *Download calendar file* adds every expiry and due date, with alarms, to Apple / Google / Outlook calendar. Download it again after adding documents. |
| **Dashboard** | The Reminders card, with **Paid** / **Done** to stop an item (and Undo). |

Nothing is sent by email or through any online service: phone reminders are scheduled on the
phone itself.

## Phone app (Phase 7)
**Send to the vault from any app:** in WhatsApp, Files, Gmail or Photos, tap **Share → Document Vault**.
The app opens on **Scan** and uploads it like a scan (locked PDFs ask for their password; with the Mac
off it waits on the phone). PDFs, photos and Word files.

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
| `backend/security/` | Encryption at rest (`crypto.py`), the vault key in the Keychain (`keys.py`), encrypted backups (`backup.py`), activity log (`access_log.py`), terminal tool (`cli.py`) |
| `backend/auth/` | Sign-in: password hashing (`passwords.py`), accounts and sessions (`accounts.py`), passkeys (`passkeys.py`), paired phones (`devices.py`), terminal tool (`cli.py`) |
| `backend/api/lan.py` | The phone listener's certificate and the Mac's Wi-Fi address |
| `mobile/` | The Flutter phone app (see `mobile/README.md`) |
| `backend/reminders/` | Reminder rules, phone schedule, calendar file (`reminders.py`); Mac notifications (`desktop.py`) |
| `backend/extract/` | Key facts: readers and checks (`facts.py`), filling in older documents (`run.py`), Needle setup (`needle_setup.py`) |
| `backend/classify/` | Sorting documents into types (`classifier.py`), re-sorting older ones (`run.py`) |
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
151 tests. They use a fake embedder and a fake LLM, so they run in a few seconds without
downloading models or running Ollama. By default they use SQLite; to run them against
PostgreSQL (each test gets a throwaway database):
```bash
VAULT_TEST_DATABASE_URL=postgresql+psycopg://localhost/postgres pytest -q
```
