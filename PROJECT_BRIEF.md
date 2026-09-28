# Family Documents Assistant: Project Brief

## Goal
A private assistant for a family's paperwork. Upload insurance policies, property documents,
bills and warranty cards once; later, ask in plain language and get the answer with the page
it came from:

- "When does my bike insurance expire?"
- "Find the washing machine warranty."
- "How much was the electricity bill in August?"
- "What is the carpet area of our flat?"

Under the hood it is a RAG system: hybrid retrieval (vector + keyword) with reranking, and
a measured score at every stage. This is a learning and resume project, so depth and honest
evaluation matter more than speed.

## Requirements
**Documents**
- Upload from the web page (drag and drop, several at once) or import a folder from the command line.
- Formats: PDF (text or scanned), Word (.docx), text/Markdown, and photos of documents (JPG/PNG/WebP/TIFF).
  Scans and photos are read with OCR (Tesseract).
- Categories: insurance, warranty, bill, property, other. Picked on upload or auto-detected.
- Browse the library by category, open the original file, remove documents.
- Re-uploading the same file is detected and skipped.

**Questions**
- Ask in natural language; results show the document, its category and the page, with the
  matching passage highlighted and a link that opens the file at that page.
- Filter by category.
- Later: a written answer from an LLM, citing the passages it used.

**Privacy**
- Everything runs locally: files, extracted text and search index stay under `data/`
  (git-ignored). The server listens on 127.0.0.1 only; there are no accounts.
- Only the step 6 LLM call sends text off the machine, and only the few passages needed to
  answer. This will be clearly marked and optional.

## Tech stack
- **Parsing**: `pypdfium2` (PDF text, and rendering scanned pages), `python-docx`, `pytesseract` + Tesseract (OCR)
- **Chunking**: page-aware token windows (300 tokens, 60 overlap, measured with the embedding model's
  tokenizer), so every chunk knows its document and page
- **Embeddings**: `sentence-transformers`, `BAAI/bge-small-en-v1.5`
- **Index**: NumPy, exact cosine search, persisted to disk (a family has hundreds of pages, not millions)
- **Keyword search** (step 3): BM25, essential here for policy numbers, serial numbers, registration numbers and dates
- **Fusion**: Reciprocal Rank Fusion (RRF) of vector + BM25 rankings
- **Reranker** (step 4): cross-encoder `BAAI/bge-reranker-base`
- **Generation** (step 6): any LLM API behind a small interface, kept decoupled from retrieval
- **API + UI**: FastAPI serving a single-page web app
- **Containerization**: Docker Compose

## Architecture
```
upload ─> extract text per page (OCR if scanned) ─> chunk ─> embed ─> library (data/index/)

question
  -> [BM25 search]           -> candidates A      (step 3)
  -> [vector search]         -> candidates B
  -> [RRF fusion of A + B]   -> fused ranking     (step 3)
  -> [cross-encoder rerank]  -> top passages      (step 4)
  -> [LLM]                   -> answer + citations (step 6)
```

## Evaluation
Real family documents can't be committed, so the repo ships **fictional sample documents**
(`samples/documents/`: 13 documents across the four categories) and a **question set**
(`samples/questions.jsonl`: 24 questions, each with its correct document and exact answer text).

The samples include realistic look-alikes, so the task isn't trivial: last year's and this
year's bike policy, four appliance warranties, three kinds of bill.

Metrics (see `src/eval/metrics.py`):
- **Recall@k**: correct document in the top k
- **MRR@5**: how high the correct document ranks
- **Answer@k**: the answer text itself is in one of the top-k passages; that is what an LLM needs

`python -m src.eval.evaluate` builds a throwaway library from the samples, scores it, and saves
`results/eval_<method>.json`. Users can write a question file for their own documents too.

## Build order (each step ends working and measured)
1. ✅ **Ingestion**: PDF/Word/text/photo parsing with OCR, page-aware chunking, categories, library on disk
2. ✅ **Vector search + web app**: upload, browse, delete, search with category filter; baseline score on the sample questions
3. **Hybrid search**: BM25 + RRF fusion. Measure against step 2, especially on number-heavy questions
4. **Reranking**: cross-encoder over the fused top 20. Measure against step 3
5. **Key facts**: pull expiry dates, due dates, amounts and policy numbers into structured fields;
   prefer the current policy over expired ones; "what expires in the next 60 days" view
6. **Answers**: LLM answer with citations to page-level passages; measure answer accuracy on the question set
7. **Docker**: `docker compose up` runs the whole app
8. (Stretch) Reminders for upcoming expiries and due dates; multi-user household accounts

## Definition of done for v1
- Upload real documents (including a phone photo) and get correct, cited answers
- A documented score for each retrieval strategy (vector vs hybrid vs hybrid + rerank) on the
  sample question set, so the improvement is shown, not claimed
- Runs via `docker compose up`
- README explains the architecture and results

## Notes for whoever picks this up
- Keep retrieval and generation decoupled: the engineering and the eval story are in retrieval.
- Save the eval number at every step before moving on. The before/after deltas are the point.
- Never commit anything under `data/`: it is the family's personal documents.
