# Secure Digital Document Vault: Project Brief

## Vision
A private vault for one family's important papers: insurance policies, warranty cards,
property documents, bills, medical and banking records. Upload a PDF or snap a photo once;
later, just ask:

- "When does my bike insurance expire?" → *"Your insurance expires on 14 March 2027."* (with the page it came from)
- "Find the washing machine warranty." → the warranty card, ready to open
- "What is the carpet area of our flat?" → *"1,085 sq ft"*, from the sale deed

It also watches the dates for you: warranties and policies about to expire trigger a reminder.

## Who it's for: our family only
This is not a public product. It is built for, and used only by, members of one family.
That shapes several decisions:

- **Invite-only accounts.** No public sign-up. Logins are limited to an allowlist of family
  email addresses (Phase 6). Each person sees their own documents plus what's shared with them (Phase 12).
- **Documents never go into the repository.** Uploads, extracted text and indexes live under
  `data/` (git-ignored) locally, or in private storage when deployed. The GitHub repo is private.
- **Minimum data leaves the vault.** Only the few passages needed to answer a question are
  sent to the LLM, and a fully local LLM is supported (see *LLM choice*).
- **Security is a phase of its own** (Phase 11): encryption at rest, signed URLs, HTTPS.

## How we build it: 13 phases
Each phase ends with something that works and can be demonstrated. Building mobile, web, RAG,
OCR, auth, reminders and security at once is how projects stall, so one phase at a time.

### Phase 1: Core RAG engine (backend only)
**Goal:** answer questions from uploaded documents.
- Upload PDF → extract text → chunk → embed (BGE Small v1.5) → store vectors (FAISS)
- Retrieve top-k chunks → send them as context to the LLM → return the answer

```text
backend/
├── upload/
├── parser/
├── chunker/
├── embeddings/
├── vector_store/
├── retriever/
└── llm/
```
**Test:** upload `Bike Insurance 2026.pdf`, ask "When does my insurance expire?",
get "Your insurance expires on 14 March 2027."

### Phase 2: Document metadata
**Goal:** manage documents properly.
- Store document name, upload date, document type, owner, file path.
- Database: **PostgreSQL**, tables `users`, `documents`, `document_chunks`.
- Show "My Documents": ✓ Bike Insurance ✓ Washing Machine Warranty ✓ House Registration.

### Phase 3: Web application
**Goal:** make it usable.
- Frontend: **React + Next.js**.
- Pages: Login, Dashboard, Upload, Chat, Documents, Settings.
- Dashboard: document count by type (Insurance 4, Warranty 3, Property 2, Bills 3...).

### Phase 4: OCR
**Goal:** support photos of warranty cards, insurance papers and property papers.
- **Tesseract** (or PaddleOCR).
- Flow: image → OCR → text → chunk → embed.

### Phase 5: Better retrieval
**Goal:** hybrid search, BM25 + vector.
- Keyword search catches exact terms: "Bike Insurance", policy numbers, serial numbers.
- Embeddings catch meaning: "my two wheeler policy" finds the bike insurance.
- Fuse both rankings with Reciprocal Rank Fusion; optionally rerank with a cross-encoder.
- Measured against Phase 1 on the sample question set (see *Evaluation*).

### Phase 6: Authentication
**Goal:** multiple users, each seeing only their own documents.
- Google OAuth or email + password, **restricted to allowlisted family members**.
- Tables: `users`, `documents`, `permissions`.

### Phase 7: Mobile app
**Goal:** real-world use.
- **Flutter.** Screens: Splash, Login, Dashboard, Upload, Camera, Chat, Profile.
- Camera upload: open camera → take picture → OCR → store.

### Phase 8: Document classification
**Goal:** detect the type automatically instead of asking the user.
- Insurance, Warranty, Property, Medical, Banking, Education (and Bills), stored in the database.

### Phase 9: Expiry extraction
**Goal:** pull key dates out of documents.
- Policy expiry, warranty expiry, renewal date. For example "Warranty valid until: 12/08/2028" → stored as a date.
- When several versions exist (last year's and this year's policy), the latest one wins.

### Phase 10: Reminder system
**Goal:** never miss a renewal.
- Background job checks `expiry_date - today`; under 30 days → push notification:
  *"Your bike insurance expires in 25 days."*

### Phase 11: Security
- Files encrypted at rest (AES).
- Signed, expiring URLs instead of `/storage/file.pdf`.
- HTTPS everywhere.

### Phase 12: Family sharing
- Example: father uploads the property document and shares it with son and daughter.
- Permissions: View, Download, Chat.

### Phase 13: Production deployment
- Backend: Docker + FastAPI. Database: PostgreSQL. Vectors: FAISS initially, Qdrant later.
- Hosting: Render, Railway or AWS. Because it's family-only, a home server reachable over a
  private network (e.g. Tailscale) is also a good option: nothing is exposed to the public internet.

## Final architecture
```text
   Flutter App        React Web App
        \                 /
         v               v
              FastAPI
                 |
   ---------------------------------
   |              |                |
PostgreSQL     Qdrant         File store
(metadata)   (embeddings)   (encrypted files)
                 |
          BGE Small v1.5
                 |
                LLM
  (Gemini, or a local Llama / Gemma / Mistral)
```

## LLM choice
The original plan listed Gemini or Qwen. Qwen is replaced with alternatives from providers
we're comfortable sending family documents to, or with models that run entirely on our own machine.
The `llm/` module hides the provider behind one interface, so switching is a config change.

| Option | Where it runs | Notes |
|---|---|---|
| **Gemini** (Google) | Cloud API | Good quality, cheap. Check data-use terms: Google's free API tier may use submitted content to improve its products; the paid tier does not. Use a paid key for real documents. |
| **Claude** (Anthropic) or **GPT** (OpenAI) | Cloud API | Strong answer quality. Their APIs don't train on API data by default; still, passages leave the machine. |
| **Llama** (Meta), **Gemma** (Google), **Mistral** (France) via **Ollama** | Your own computer | Nothing leaves the machine: the most private option. Small models (7-9B) run on a 16 GB Mac; answers are a bit weaker. |

Recommended: start with a **local model through Ollama** for real family documents, and keep a
cloud option (Gemini paid tier) switchable for comparison.

## Evaluation
Real family documents can't be committed, so the repo ships a fictional family's papers:
- `samples/documents/`: 13 documents (insurance, warranty, bill, property) as text,
- `samples/pdfs/`: the same 13 as PDFs to upload (e.g. `Bike Insurance 2026.pdf`),
- `samples/questions.jsonl`: 24 questions, each with its correct document and exact answer.

The samples include look-alikes so the task isn't trivial: last year's and this year's bike
policy, four appliance warranties, three kinds of bill.

Metrics: **Recall@k** (right document in the top k), **MRR@5** (how high it ranks), **Answer@k**
(the answer text is in a retrieved passage), and from Phase 1's LLM step, **answer accuracy**.
Every retrieval change (Phase 5 hybrid, reranking) is measured against the previous score
before it's kept. The before/after numbers are the point.

## Current state (what exists before Phase 1 is finished)
An early prototype already covers parts of several phases. It gets reorganized as the phases
are built:

| Phase | Already in the prototype | Still to do |
|---|---|---|
| 1 Core RAG | Upload, PDF/Word/text parsing, page-aware chunking, BGE Small embeddings, top-k retrieval, FastAPI | Move code into the `backend/` layout, FAISS vector store, **LLM answer** |
| 2 Metadata | Name, type, upload date, pages, file path, in a JSON file | PostgreSQL, owner |
| 3 Web app | Single-page HTML app: upload, library by category, search | React + Next.js, login, chat |
| 4 OCR | Tesseract for photos and scanned PDFs | PaddleOCR comparison (optional) |
| 5 Retrieval | Vector search, category filter, evaluation harness | BM25 + fusion, reranking |
| 8 Classification | Keyword-based guess (insurance/warranty/bill/property/other) | ML/LLM classifier, more types |
| 6, 7, 9–13 | – | Everything |

## Notes for whoever picks this up
- Follow the phases in order; each one ends demonstrable.
- Keep retrieval and generation decoupled: retrieval is where the measurable engineering is.
- Never commit anything under `data/`: it is the family's personal documents.
