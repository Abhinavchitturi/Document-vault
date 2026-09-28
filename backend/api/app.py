"""Secure Digital Document Vault: web API and page.

Endpoints:
    GET    /                              the web app (frontend/out, built with `npm run build`)
    GET    /api/info                      library size, categories, whether OCR is available
    GET    /api/documents                 all documents in the library
    PATCH  /api/documents/{id}            change a document's type
    GET    /api/settings                  owner, models, where data lives
    POST   /api/settings                  switch the answer model (one already downloaded in Ollama)
    POST   /api/documents                 upload one or more files (form fields: files, category)
    GET    /api/documents/{id}/file       open the original file
    GET    /api/documents/{id}/pages/{n}/image       one page as an image (scans and photos straightened)
    POST   /api/documents/{id}/pages/{n}/highlights  where the answer's passage and facts are on that page
    DELETE /api/documents/{id}            remove a document and its search data
    GET    /api/search?q=...&category=... best-matching passage per document
    POST   /api/chat                      chat: answer streamed word by word (NDJSON), with sources
    POST   /api/ask                       the same answer in one response (scripts, tests)

Usage:
    python -m backend.api.app                 # then open http://127.0.0.1:8000
    python -m backend.api.app --llm-model gemma2:9b

Answers come from a local model through Ollama (see backend/llm/ollama.py); if
Ollama isn't running, search still works and the page says how to start it.

The server listens on 127.0.0.1 only: the library holds personal documents and
there is no login, so it must not be exposed to a network.
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from typing import Literal

from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.llm.answer import LLM, answer_question, finalize, stream_answer
from backend.llm.ollama import LLMUnavailableError
from backend.retriever.retriever import Retriever
from backend.viewer.highlight import highlights_for, page_image

from backend.parser.categories import CATEGORIES
from backend.parser.extract import IMAGE_SUFFIXES, SUPPORTED_SUFFIXES, OcrUnavailableError, UnsupportedFileError, ocr_available
from backend.upload.ingest import EmbedderLike, EmptyDocumentError, ingest_file
from backend.vector_store.store import DocumentStore

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "out"

# The page may only talk to this server: no scripts, fonts, images or requests from anywhere
# else. Applied to HTML pages only (a strict policy can break the browser's built-in PDF viewer).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Document Vault</title>
<body style="font:16px/1.6 -apple-system,sans-serif;max-width:560px;margin:10vh auto;padding:0 16px">
<h1>Almost there</h1><p>The web app hasn't been built yet. In a terminal, from the project folder:</p>
<pre style="background:#f1f2f7;padding:12px;border-radius:8px">cd frontend
npm install
npm run build</pre><p>Then restart <code>python -m backend.api.app</code> and reload this page.</p></body>"""
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

logger = logging.getLogger("api")


def _public(doc: dict) -> dict:
    """Document record as sent to the browser (no internal storage details)."""
    return {k: v for k, v in doc.items() if k not in ("sha256", "stored_as")}


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    category: str | None = None
    k: int = Field(5, ge=1, le=10)


class CategoryUpdate(BaseModel):
    category: str


class SettingsUpdate(BaseModel):
    llm_model: str | None = Field(None, min_length=1, max_length=200)
    owner_name: str | None = Field(None, min_length=1, max_length=60)


class HighlightRequest(BaseModel):
    chunk_id: str | None = Field(None, max_length=100)  # the passage the answer used
    answer: str | None = Field(None, max_length=4000)  # the answer text, to find its facts on the page


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    category: str | None = None
    k: int = Field(5, ge=1, le=10)  # passages given to the LLM


def _passage(chunk: dict, score: float, rank: int) -> dict:
    return {
        "rank": rank,
        "score": round(score, 4),
        "chunk_id": chunk["chunk_id"],
        "doc_id": chunk["doc_id"],
        "filename": chunk["filename"],
        "category": chunk["category"],
        "page": chunk["page"],
        "text": chunk["text"],
    }


def _llm_status(llm) -> dict:
    if llm is None:
        return {"ready": False, "message": "Answers are turned off (the app was started with --no-llm)."}
    check = getattr(llm, "status", None)
    ready, message = check() if check else (True, f"Answers ready ({llm.name})")
    return {"ready": ready, "message": message}


def create_app(
    store: DocumentStore,
    embedder: EmbedderLike,
    tokenizer,
    llm: LLM | None = None,
    frontend_dir: Path | None = FRONTEND_DIR,
    database_label: str = "",
) -> FastAPI:
    app = FastAPI(title="Secure Digital Document Vault", docs_url=None, redoc_url=None, openapi_url=None)
    retriever = Retriever(store, embedder)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    @app.get("/api/info")
    def info() -> dict:
        counts = Counter(d["category"] for d in store.documents.values())
        return {
            "model": embedder.model_name,
            "owner": store.owner,
            "llm": llm.name if llm else None,
            "llm_status": _llm_status(llm),
            "num_documents": len(store.documents),
            "num_chunks": len(store.chunks),
            "categories": {c: counts.get(c, 0) for c in CATEGORIES},
            "ocr_available": ocr_available(),
            "supported_types": sorted(SUPPORTED_SUFFIXES),
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        }

    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        return [_public(d) for d in store.list_documents()]

    @app.post("/api/documents")
    def upload(files: list[UploadFile] = File(...), category: str = Form("auto")) -> list[dict]:
        if category != "auto" and category not in CATEGORIES:
            raise HTTPException(400, f"category must be 'auto' or one of {CATEGORIES}")
        results = []
        for upload_file in files:
            name = Path(upload_file.filename or "document").name
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                results.append({"filename": name, "status": "error",
                                "error": f"Unsupported file type. Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"})
                continue
            with tempfile.TemporaryDirectory() as tmp:
                # Keep the extension: extraction picks its parser from it.
                tmp_path = Path(tmp) / f"upload{suffix}"
                size = 0
                with open(tmp_path, "wb") as out:
                    while block := upload_file.file.read(1 << 20):
                        size += len(block)
                        if size > MAX_UPLOAD_BYTES:
                            break
                        out.write(block)
                if size > MAX_UPLOAD_BYTES:
                    results.append({"filename": name, "status": "error",
                                    "error": f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"})
                    continue
                try:
                    result = ingest_file(tmp_path, store, embedder, tokenizer, category, filename=name)
                except (UnsupportedFileError, OcrUnavailableError, EmptyDocumentError, ValueError) as e:
                    results.append({"filename": name, "status": "error", "error": str(e)})
                    continue
                except Exception:  # a corrupt file shouldn't take the server down
                    logger.exception("Failed to ingest %s", name)
                    results.append({"filename": name, "status": "error", "error": "Could not read this file."})
                    continue
            results.append({"filename": name, "status": "added" if result.created else "duplicate",
                            "document": _public(result.document)})
        return results

    @app.get("/api/documents/{doc_id}/file")
    def open_file(doc_id: str) -> FileResponse:
        doc = store.get(doc_id)
        path = store.file_path(doc_id)
        if not doc or not path or not path.exists():
            raise HTTPException(404, "document not found")
        return FileResponse(path, filename=doc["filename"], content_disposition_type="inline")

    def _owned_file(doc_id: str) -> tuple[dict, Path]:
        doc = store.get(doc_id)  # only this owner's documents are visible
        path = store.file_path(doc_id)
        if not doc or not path or not path.exists():
            raise HTTPException(404, "document not found")
        return doc, path

    @app.get("/api/documents/{doc_id}/pages/{page}/image")
    def page_image_route(doc_id: str, page: int) -> Response:
        _, path = _owned_file(doc_id)
        if path.suffix.lower() != ".pdf" and path.suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(400, "page images are only available for PDFs, scans and photos")
        try:
            image, media_type = page_image(path, page)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return Response(image, media_type=media_type, headers={"Cache-Control": "private, no-store"})

    @app.post("/api/documents/{doc_id}/pages/{page}/highlights")
    def page_highlights(doc_id: str, page: int, req: HighlightRequest) -> dict:
        _, path = _owned_file(doc_id)
        chunk = next((c for c in store.chunks if c["chunk_id"] == req.chunk_id and c["doc_id"] == doc_id), None)
        try:
            result = highlights_for(path, page, chunk["text"] if chunk else None, req.answer)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return asdict(result)

    @app.patch("/api/documents/{doc_id}")
    def update_document(doc_id: str, req: CategoryUpdate) -> dict:
        if req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        doc = store.get(doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        from backend.upload.ingest import passage_texts

        chunks = [{**c, "category": req.category} for c in store.chunks_of(doc_id)]
        vectors = embedder.encode_passages(passage_texts(chunks)) if chunks else None
        return _public(store.update_category(doc_id, req.category, vectors))

    def settings_view() -> dict:
        list_models = getattr(llm, "list_models", None)
        return {
            "owner": store.owner,
            "llm_model": getattr(llm, "model", None) if llm else None,
            "llm_models": list_models() if list_models else [],
            "llm_status": _llm_status(llm),
            "embedding_model": embedder.model_name,
            "database": database_label or "SQLite file in the vault folder",
            "index_dir": str(store.root),
            "ocr_available": ocr_available(),
        }

    @app.get("/api/settings")
    def get_settings() -> dict:
        return settings_view()

    @app.post("/api/settings")
    def save_settings(req: SettingsUpdate) -> dict:
        if req.owner_name is not None:
            try:
                store.rename_owner(req.owner_name)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            logger.info("Owner renamed to %s", store.owner)
        if req.llm_model is None:
            return settings_view()
        if llm is None or not hasattr(llm, "model"):
            raise HTTPException(400, "Answers are turned off; start the app without --no-llm to choose a model.")
        installed = llm.list_models() if hasattr(llm, "list_models") else []
        if req.llm_model not in installed:
            raise HTTPException(400, f"{req.llm_model} isn't downloaded. Run: ollama pull {req.llm_model}")
        llm.model = req.llm_model
        logger.info("Answer model switched to %s", req.llm_model)
        return settings_view()

    @app.delete("/api/documents/{doc_id}")
    def delete(doc_id: str) -> dict:
        if not store.delete(doc_id):
            raise HTTPException(404, "document not found")
        return {"deleted": doc_id}

    @app.get("/api/search")
    def search(
        q: str = Query(..., min_length=1, max_length=500),
        k: int = Query(5, ge=1, le=20),
        category: str | None = None,
    ) -> dict:
        if category and category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        t0 = time.perf_counter()
        hits = retriever.documents(q, top_k=k, category=category or None)
        took_ms = (time.perf_counter() - t0) * 1000
        return {
            "question": q,
            "category": category,
            "method": "vector",
            "took_ms": round(took_ms, 1),
            "results": [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)],
        }

    def chat_context(question: str, history: list[ChatTurn], k: int, category: str | None):
        """Passages for a chat turn. A follow-up ("and when is it due?") says little on its
        own, so also search with the previous question and add those passages at the end."""
        hits = retriever.context(question, top_k=k, category=category)
        previous = next((t.content for t in reversed(history) if t.role == "user"), None)
        if previous:
            seen = {c["chunk_id"] for c, _ in hits}
            for chunk, score in retriever.context(f"{previous} {question}", top_k=3, category=category):
                if chunk["chunk_id"] not in seen:
                    hits.append((chunk, score))
                    seen.add(chunk["chunk_id"])
        return hits

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> StreamingResponse:
        if req.category and req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        if llm is None:
            raise HTTPException(503, "Answers are turned off (the app was started with --no-llm).")
        hits = chat_context(req.question, req.history, req.k, req.category or None)
        sources = [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)]
        history = [t.model_dump() for t in req.history]

        def events():
            yield json.dumps({"type": "sources", "sources": sources}) + "\n"
            text = ""
            try:
                for piece in stream_answer(req.question, [c for c, _ in hits], llm, history=history):
                    text += piece
                    yield json.dumps({"type": "token", "text": piece}) + "\n"
            except LLMUnavailableError as e:
                yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                return
            except Exception:  # never leave the chat bubble hanging
                logger.exception("Answer failed")
                yield json.dumps({"type": "error", "message": "Something went wrong while writing the answer."}) + "\n"
                return
            answer = finalize(text, len(hits))
            yield json.dumps({"type": "done", "citations": answer.citations, "found": answer.found,
                              "llm": llm.name}) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        if req.category and req.category not in CATEGORIES:
            raise HTTPException(400, f"category must be one of {CATEGORIES}")
        if llm is None:
            raise HTTPException(503, "No local LLM is configured. Start the app without --no-llm.")
        t0 = time.perf_counter()
        hits = retriever.context(req.question, top_k=req.k, category=req.category or None)
        retrieval_ms = (time.perf_counter() - t0) * 1000
        try:
            answer = answer_question(req.question, [chunk for chunk, _ in hits], llm)
        except LLMUnavailableError as e:
            raise HTTPException(503, str(e)) from e
        return {
            "question": req.question,
            "answer": answer.text,
            "found": answer.found,
            "citations": answer.citations,
            "sources": [_passage(chunk, score, i + 1) for i, (chunk, score) in enumerate(hits)],
            "llm": llm.name,
            "retrieval_ms": round(retrieval_ms, 1),
            "took_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    if frontend_dir and (frontend_dir / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="web")  # after the API routes
    else:
        @app.get("/", include_in_schema=False)
        def not_built() -> Response:
            return Response(NOT_BUILT, media_type="text/html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", default="data/index", help="FAISS vectors and original files")
    parser.add_argument("--database-url", default=None,
                        help="metadata database (default: $VAULT_DATABASE_URL or postgresql+psycopg://localhost/vault)")
    parser.add_argument("--owner", default=None, help="whose documents (default: $VAULT_OWNER or your user name)")
    parser.add_argument("--model", default=None, help="embedding model (default: snowflake-arctic-embed-m-v1.5)")
    parser.add_argument("--device", default=None, help="cuda / mps / cpu; default picks automatically")
    parser.add_argument("--llm-model", default=None, help="local Ollama model (default: llama3.1:8b)")
    parser.add_argument("--no-llm", action="store_true", help="search only, no answers")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    from backend.chunker.chunker import HFTokenizer
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder
    from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
    from backend.llm.ollama import OllamaLLM
    from backend.upload.ingest import passage_texts

    from backend.database.db import DatabaseUnavailableError, database_url, default_owner

    try:
        store = DocumentStore(args.index_dir, database_url(args.database_url), owner=default_owner(args.owner))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    embedder = Embedder(args.model or DEFAULT_MODEL, device=args.device)
    if store.model and store.model != embedder.model_name:
        logger.info("Vault was embedded with %s; re-embedding with %s", store.model, embedder.model_name)
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    elif store.missing_vectors:
        logger.warning("Some passages have no search vectors (interrupted save?); rebuilding them")
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    if not ocr_available():
        logger.warning("Tesseract not found: photos and scanned PDFs can't be read. macOS: brew install tesseract")

    llm = None if args.no_llm else OllamaLLM(args.llm_model or DEFAULT_LLM)
    from sqlalchemy.engine import make_url

    db_label = make_url(database_url(args.database_url)).render_as_string(hide_password=True)
    app = create_app(store, embedder, HFTokenizer(embedder.model_name), llm, database_label=db_label)
    logger.info("Vault for %s: %d documents. Answers: %s. Open http://127.0.0.1:%d",
                store.owner, len(store.documents), llm.name if llm else "off", args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
