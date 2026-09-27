"""Token-window chunking with overlap.

Chunks are measured in tokens of the *embedding model's* tokenizer (default
BAAI/bge-small-en-v1.5), because that is the budget that actually matters: bge
truncates at 512 tokens including [CLS]/[SEP], so chunk_size must stay <= 510.

Tokenizers here return character spans rather than token strings, so each chunk's
text is an exact slice of the original document (original casing/whitespace kept,
no lossy detokenization).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Iterator, Protocol

from src.ingestion.loader import Document

DEFAULT_TOKENIZER = "BAAI/bge-small-en-v1.5"
MODEL_MAX_TOKENS = 510  # 512 minus [CLS] and [SEP]


class SpanTokenizer(Protocol):
    name: str

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Return (start_char, end_char) for each token in `text`."""
        ...


class HFTokenizer:
    """Wraps a Hugging Face fast tokenizer to produce character spans."""

    def __init__(self, model_name: str = DEFAULT_TOKENIZER):
        from transformers import AutoTokenizer

        self.name = model_name
        self._tok = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        # We only count tokens here; silence the "sequence longer than 512" warning.
        self._tok.model_max_length = 10**9

    def spans(self, text: str) -> list[tuple[int, int]]:
        enc = self._tok(text, add_special_tokens=False, return_offsets_mapping=True)
        return [tuple(span) for span in enc["offset_mapping"]]


class RegexTokenizer:
    """Dependency-free approximation: words and individual punctuation marks.

    Undercounts WordPiece tokens somewhat (rare words split into several pieces),
    so prefer HFTokenizer for real runs. Useful for tests and offline work.
    """

    name = "regex"
    _pattern = re.compile(r"\w+|[^\w\s]")

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [m.span() for m in self._pattern.finditer(text)]


@dataclass
class Chunk:
    chunk_id: str  # f"{doc_id}#{chunk_index}"
    doc_id: str
    source: str  # dataset the doc came from, e.g. "beir/nq"
    title: str
    text: str
    chunk_index: int  # position of this chunk within its document
    num_chunks: int  # total chunks produced from the document
    start_char: int  # span of `text` within the original document text
    end_char: int
    n_tokens: int

    def to_dict(self) -> dict:
        return asdict(self)


def chunk_document(
    doc: Document,
    tokenizer: SpanTokenizer,
    source: str,
    chunk_size: int = 400,
    overlap: int = 80,
) -> list[Chunk]:
    """Split one document into overlapping windows of `chunk_size` tokens.

    Consecutive chunks share `overlap` tokens. A document at or under
    `chunk_size` tokens becomes a single chunk. If the final window would be a
    short tail (< overlap new tokens), it is folded into the previous window
    instead of emitted as a near-duplicate fragment.
    """
    if not 0 <= overlap < chunk_size:
        raise ValueError("need 0 <= overlap < chunk_size")
    if chunk_size > MODEL_MAX_TOKENS:
        raise ValueError(f"chunk_size {chunk_size} exceeds embedding model limit {MODEL_MAX_TOKENS}")

    spans = tokenizer.spans(doc.text)
    if not spans:
        return []

    stride = chunk_size - overlap
    windows: list[tuple[int, int]] = []  # token index ranges [start, end)
    start = 0
    while True:
        end = min(start + chunk_size, len(spans))
        windows.append((start, end))
        if end == len(spans):
            break
        start += stride
    # Fold a tiny trailing window into its predecessor, as long as the merged
    # window still fits the embedding model.
    if (
        len(windows) > 1
        and windows[-1][1] - windows[-2][1] < overlap
        and windows[-1][1] - windows[-2][0] <= MODEL_MAX_TOKENS
    ):
        last_end = windows.pop()[1]
        windows[-1] = (windows[-1][0], last_end)

    chunks = []
    for i, (tok_start, tok_end) in enumerate(windows):
        start_char = spans[tok_start][0]
        end_char = spans[tok_end - 1][1]
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#{i}",
                doc_id=doc.doc_id,
                source=source,
                title=doc.title,
                text=doc.text[start_char:end_char],
                chunk_index=i,
                num_chunks=len(windows),
                start_char=start_char,
                end_char=end_char,
                n_tokens=tok_end - tok_start,
            )
        )
    return chunks


def chunk_documents(
    docs: Iterable[Document],
    tokenizer: SpanTokenizer,
    source: str,
    chunk_size: int = 400,
    overlap: int = 80,
) -> Iterator[Chunk]:
    for doc in docs:
        yield from chunk_document(doc, tokenizer, source, chunk_size, overlap)
