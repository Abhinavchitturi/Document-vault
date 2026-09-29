"""Add documents to the library: extract text, chunk it, embed it, store it.

Used by the web app's upload button, and from the command line for importing a
whole folder at once:

    python -m backend.upload.ingest ~/Documents/family-docs
    python -m backend.upload.ingest bike_policy.pdf --category insurance

When importing a folder with --category auto (the default), a file inside a
folder named after a category (insurance/, warranty/, bill/, property/) gets
that category; anything else is guessed from its text.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from backend.classify.classifier import Classifier
from backend.extract.facts import default_reader
from backend.parser.categories import CATEGORIES
from backend.chunker.chunker import SpanTokenizer, chunk_text
from backend.parser.extract import SUPPORTED_SUFFIXES, extract_pages
from backend.parser.ocr import save_ocr
from backend.vector_store.store import DocumentStore
from backend.embeddings.embedder import format_passage

logger = logging.getLogger(__name__)


class EmbedderLike(Protocol):
    model_name: str

    def encode_passages(self, texts: list[str]) -> np.ndarray: ...

    def encode_queries(self, texts: list[str]) -> np.ndarray: ...


class EmptyDocumentError(ValueError):
    pass


@dataclass
class IngestResult:
    document: dict
    created: bool  # False if this exact file was already in the library


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def passage_texts(chunks: list[dict]) -> list[str]:
    """What gets embedded for each chunk: the document's name and category, then
    the chunk text. The name often carries the key fact the text never repeats
    ("bike insurance 2026.pdf" whose pages only say "two-wheeler")."""
    return [format_passage(f"{Path(c['filename']).stem.replace('_', ' ')} ({c['category']})", c["text"]) for c in chunks]


def _facts_for(category: str, facts) -> dict:
    """A bill's billing period isn't an expiry, whichever reader was fooled by it."""
    fields = facts.as_document_fields()
    if category == "bill":
        fields["expires_on"] = None
    return fields


def ingest_file(
    path: str | Path,
    store: DocumentStore,
    embedder: EmbedderLike,
    tokenizer: SpanTokenizer,
    category: str = "auto",
    filename: str | None = None,
    chunk_size: int = 300,
    overlap: int = 60,
    classifier=None,
    facts_reader=None,
) -> IngestResult:
    """Add one file. `filename` is the name to show (uploads arrive as temp files,
    saved with the upload's extension so the right parser is picked)."""
    path = Path(path)
    filename = Path(filename or path.name).name  # never trust a client-supplied path
    suffix = path.suffix.lower()
    if category != "auto" and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; use one of {CATEGORIES} or 'auto'")

    sha256 = file_sha256(path)
    existing = store.find_by_sha256(sha256)
    if existing:
        return IngestResult(existing, created=False)

    pages = extract_pages(path)
    full_text = "\n".join(p.text for p in pages)
    if not full_text.strip():
        if any(p.ocr for p in pages):
            raise EmptyDocumentError(
                f"No readable text found in {filename}. If it's a photo, retake it flat, in good light and in focus."
            )
        raise EmptyDocumentError(f"No text found in {filename}")
    if category == "auto":  # Phase 8: sorted by the vault (backend/classify/)
        decision = (classifier or Classifier()).classify(filename, full_text)
        category, category_source, category_confidence = decision.category, "auto", decision.confidence
    else:
        category_source, category_confidence = "person", 100

    doc_id = uuid.uuid4().hex[:16]  # not derived from the file: two family members may upload the same one
    chunks = []
    for page in pages:
        for span in chunk_text(page.text, tokenizer, chunk_size, overlap):
            chunks.append(
                {
                    "chunk_id": f"{doc_id}-p{page.number}-{len(chunks)}",
                    "doc_id": doc_id,
                    "filename": filename,
                    "category": category,
                    "page": page.number,
                    "text": page.text[span.start_char : span.end_char],
                    "n_tokens": span.n_tokens,
                }
            )

    stored_as = f"{doc_id}{suffix}"
    shutil.copyfile(path, store.files_dir / stored_as)
    scanned = {p.number: p.ocr_data for p in pages if p.ocr_data is not None}
    if scanned:  # word positions, so answers can be highlighted on the scan later
        save_ocr(store.files_dir / stored_as, scanned)
    confidences = [d.confidence for d in scanned.values() if d.confidence is not None]
    document = {
        "doc_id": doc_id,
        "filename": filename,
        "category": category,
        "pages": len(pages),
        "ocr_pages": sum(p.ocr for p in pages),
        "ocr_confidence": min(confidences) if confidences else None,  # the hardest page to read
        "category_source": category_source,
        "category_confidence": category_confidence,
        **_facts_for(category, (facts_reader or default_reader()).read(full_text)),  # Phase 9: expiry, due date, amount...
        "num_chunks": len(chunks),
        "size_bytes": path.stat().st_size,
        "sha256": sha256,
        "stored_as": stored_as,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    embeddings = embedder.encode_passages(passage_texts(chunks))
    store.add(document, chunks, embeddings, embedder.model_name)
    return IngestResult(document, created=True)


def iter_files(paths: list[Path]):
    for path in paths:
        if path.is_dir():
            yield from (p for p in sorted(path.rglob("*")) if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
        elif path.is_file():
            yield path
        else:
            logger.warning("Skipping %s: not found", path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="files or folders to import")
    parser.add_argument("--category", default="auto", choices=["auto", *CATEGORIES])
    parser.add_argument("--index-dir", default="data/index", help="FAISS vectors and original files")
    parser.add_argument("--database-url", default=None,
                        help="metadata database (default: $VAULT_DATABASE_URL or postgresql+psycopg://localhost/vault)")
    parser.add_argument("--owner", default=None, help="whose documents (default: $VAULT_OWNER or your user name)")
    parser.add_argument("--model", default=None, help="embedding model (default: snowflake-arctic-embed-m-v1.5)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from backend.chunker.chunker import HFTokenizer
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder

    from backend.database.db import DatabaseUnavailableError, database_url, default_owner

    try:
        store = DocumentStore(args.index_dir, database_url(args.database_url), owner=default_owner(args.owner))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    embedder = Embedder(args.model or DEFAULT_MODEL)
    tokenizer = HFTokenizer(embedder.model_name)
    from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
    from backend.llm.ollama import OllamaLLM

    classifier = Classifier(embedder, OllamaLLM(DEFAULT_LLM))  # asks Ollama only when unsure; fine if it's off

    added = skipped = failed = 0
    for path in iter_files(args.paths):
        category = args.category
        if category == "auto" and path.parent.name.lower() in CATEGORIES:
            category = path.parent.name.lower()
        try:
            result = ingest_file(path, store, embedder, tokenizer, category, classifier=classifier)
        except Exception as e:  # keep importing the rest of the folder
            failed += 1
            logger.error("%s: %s", path, e)
            continue
        doc = result.document
        if result.created:
            added += 1
            logger.info("Added %s [%s] %d pages, %d chunks", doc["filename"], doc["category"], doc["pages"], doc["num_chunks"])
        else:
            skipped += 1
            logger.info("Already in library: %s", path.name)
    logger.info("Done: %d added, %d already present, %d failed. Vault for %s: %d documents",
                added, skipped, failed, store.owner, len(store.documents))


if __name__ == "__main__":
    main()
