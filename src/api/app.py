"""Family Documents Assistant: web API and page.

Endpoints:
    GET    /                              the web page
    GET    /api/info                      library size, categories, whether OCR is available
    GET    /api/documents                 all documents in the library
    POST   /api/documents                 upload one or more files (form fields: files, category)
    GET    /api/documents/{id}/file       open the original file
    DELETE /api/documents/{id}            remove a document and its search data
    GET    /api/search?q=...&category=... best-matching passage per document

Usage:
    python -m src.api.app                 # then open http://127.0.0.1:8000

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

from src.ingestion.categories import CATEGORIES
from src.ingestion.extract import SUPPORTED_SUFFIXES, OcrUnavailableError, UnsupportedFileError, ocr_available
from src.ingestion.ingest import EmbedderLike, EmptyDocumentError, ingest_file
from src.retrieval.document_store import DocumentStore

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

logger = logging.getLogger("api")


def _public(doc: dict) -> dict:
    """Document record as sent to the browser (no internal storage details)."""
    return {k: v for k, v in doc.items() if k not in ("sha256", "stored_as")}


def create_app(store: DocumentStore, embedder: EmbedderLike, tokenizer) -> FastAPI:
    app = FastAPI(title="Family Documents Assistant")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/info")
    def info() -> dict:
        counts = Counter(d["category"] for d in store.documents.values())
        return {
            "model": embedder.model_name,
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
        hits = store.search(embedder.encode_queries([q])[0], top_k=k, category=category or None)
        took_ms = (time.perf_counter() - t0) * 1000
        return {
            "question": q,
            "category": category,
            "method": "vector",
            "took_ms": round(took_ms, 1),
            "results": [
                {
                    "rank": i + 1,
                    "score": round(score, 4),
                    "doc_id": chunk["doc_id"],
                    "filename": chunk["filename"],
                    "category": chunk["category"],
                    "page": chunk["page"],
                    "text": chunk["text"],
                }
                for i, (chunk, score) in enumerate(hits)
            ],
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", default="data/index")
    parser.add_argument("--model", default=None, help="embedding model (default: bge-small-en-v1.5)")
    parser.add_argument("--device", default=None, help="cuda / mps / cpu; default picks automatically")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    from src.ingestion.chunker import HFTokenizer
    from src.ingestion.ingest import passage_texts
    from src.retrieval.embedder import DEFAULT_MODEL, Embedder

    embedder = Embedder(args.model or DEFAULT_MODEL, device=args.device)
    store = DocumentStore(args.index_dir)
    if store.model and store.model != embedder.model_name:
        logger.info("Library was embedded with %s; re-embedding with %s", store.model, embedder.model_name)
        store.reembed(lambda chunks: embedder.encode_passages(passage_texts(chunks)), embedder.model_name)
    if not ocr_available():
        logger.warning("Tesseract not found: photos and scanned PDFs can't be read. macOS: brew install tesseract")

    app = create_app(store, embedder, HFTokenizer(embedder.model_name))
    logger.info("Library: %d documents. Open http://127.0.0.1:%d", len(store.documents), args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
