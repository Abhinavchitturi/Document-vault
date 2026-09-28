"""Phase 2 metadata schema (PostgreSQL).

    users            who owns documents (login comes in Phase 6)
    documents        one row per uploaded file: name, type, upload date, owner, file path
    document_chunks  the searchable pieces of each document; `id` is also the
                     chunk's id in the FAISS vector index
    vault_meta       small key/value settings, e.g. which embedding model built the vectors

Written with SQLAlchemy Core so the same code runs on PostgreSQL (the real vault)
and SQLite (the test suite, and anyone who wants a single-file setup).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), nullable=False, unique=True),
    Column("email", String(320), unique=True),  # used from Phase 6 (login)
    Column("created_at", DateTime(timezone=True), nullable=False),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String(32), primary_key=True),  # random, see backend/upload/ingest.py
    Column("owner_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text, nullable=False),  # original file name
    Column("doc_type", String(32), nullable=False),  # insurance / warranty / bill / property / other
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    Column("file_path", Text, nullable=False),  # relative to the vault's files/ folder
    Column("pages", Integer, nullable=False),
    Column("ocr_pages", Integer, nullable=False, default=0),
    Column("ocr_confidence", Integer),  # Phase 4: worst OCR page's confidence 0-100; NULL if no OCR
    Column("num_chunks", Integer, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    UniqueConstraint("owner_id", "sha256", name="uq_documents_owner_file"),
)

document_chunks = Table(
    "document_chunks",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),  # = FAISS vector id
    Column("document_id", String(32), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
    Column("page", Integer, nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("n_tokens", Integer, nullable=False),
)
Index("ix_document_chunks_document_id", document_chunks.c.document_id)

# Columns added after a table first shipped. create_all() never alters existing tables, so
# connect() adds these to older vaults: (table, column, SQL type).
ADDED_COLUMNS = [("documents", "ocr_confidence", "INTEGER")]

vault_meta = Table(
    "vault_meta",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)
