"""Secure Digital Document Vault: web API and page.

Endpoints:
    GET    /                              the web page
    GET    /api/info                      library size, categories, whether OCR is available
    GET    /api/documents                 all documents in the library
    POST   /api/documents                 upload one or more files (form fields: files, category)
    GET    /api/documents/{id}/file       open the original file
    DELETE /api/documents/{id}            remove a document and its search data
    GET    /api/search?q=...&category=... best-matching passage per document
    POST   /api/ask                       answer from the local LLM, with the cited passages

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
import logging
import tempfile
import time
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.llm.answer import LLM, answer_question
from backend.llm.ollama import LLMUnavailableError
from backend.retriever.retriever import Retriever

from backend.parser.categories import CATEGORIES
from backend.parser.extract import SUPPORTED_SUFFIXES, OcrUnavailableError, UnsupportedFileError, ocr_available
from backend.upload.ingest import EmbedderLike, EmptyDocumentError, ingest_file
from backend.vector_store.store import DocumentStore

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

logger = logging.getLogger("api")


def _public(doc: dict) -> dict:
    """Document record as sent to the browser (no internal storage details)."""
    return {k: v for k, v in doc.items() if k not in ("sha256", "stored_as")}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    category: str | None = None
    k: int = Field(5, ge=1, le=10)  # passages given to the LLM


def _passage(chunk: dict, score: float, rank: int) -> dict:
    return {
        "rank": rank,
        "score": round(score, 4),
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


def create_app(store: DocumentStore, embedder: EmbedderLike, tokenizer, llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="Secure Digital Document Vault")
    retriever = Retriever(store, embedder)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        counts = Counter(d["category"] for d in store.documents.values())
        return {
            "model": embedder.model_name,
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

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", default="data/index")
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

    embedder = Embedder(args.model or DEFAULT_MODEL, device=args.device)
    store = DocumentStore(args.index_dir)
    if store.model and store.model != embedder.model_name:
        logger.info("Library was embedded with %s; re-embedding with %s", store.model, embedder.model_name)
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    if not ocr_available():
        logger.warning("Tesseract not found: photos and scanned PDFs can't be read. macOS: brew install tesseract")

    llm = None if args.no_llm else OllamaLLM(args.llm_model or DEFAULT_LLM)
    app = create_app(store, embedder, HFTokenizer(embedder.model_name), llm)
    logger.info("Vault: %d documents. Answers: %s. Open http://127.0.0.1:%d",
                len(store.documents), llm.name if llm else "off", args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
