"""Token-window chunking with overlap.

Chunks are measured in tokens of the *embedding model's* tokenizer (default
BAAI/bge-small-en-v1.5), because that is the budget that actually matters: bge
truncates at 512 tokens including [CLS]/[SEP], so chunk_size must stay <= 510.

Tokenizers here return character spans rather than token strings, so each chunk's
text is an exact slice of the original page text (original casing/whitespace kept,
no lossy detokenization).

Defaults are smaller than typical (300 tokens, 60 overlap) because questions
about family documents target specific facts: an expiry date, a policy number,
a due amount. Smaller chunks keep the matching fact from being diluted by the
rest of a long policy page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

DEFAULT_TOKENIZER = "BAAI/bge-small-en-v1.5"
MODEL_MAX_TOKENS = 510  # 512 minus [CLS] and [SEP]
MAX_SNAP_TOKENS = 32  # how far a chunk boundary may move to land between words


class SpanTokenizer(Protocol):
    name: str

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Return (start_char, end_char) for each token in `text`."""
        ...


class HFTokenizer:
    """Wraps a Hugging Face fast tokenizer to produce character spans."""

    def __init__(self, model_name: str = DEFAULT_TOKENIZER):
        from backend.embeddings.embedder import prefer_offline

        prefer_offline(model_name)
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
class Span:
    start_char: int
    end_char: int
    n_tokens: int


def chunk_text(text: str, tokenizer: SpanTokenizer, chunk_size: int = 300, overlap: int = 60) -> list[Span]:
    """Split text into overlapping windows of up to `chunk_size` tokens.

    Consecutive chunks share about `overlap` tokens. Window boundaries are moved
    back to the nearest whitespace so no chunk starts or ends mid-word. Text at
    or under `chunk_size` tokens becomes a single chunk. If the final window
    would be a short tail (< overlap new tokens), it is folded into the previous
    window instead of emitted as a near-duplicate fragment.

    Returns character spans; `text[span.start_char:span.end_char]` is the chunk.
    """
    if not 0 <= overlap < chunk_size:
        raise ValueError("need 0 <= overlap < chunk_size")
    if chunk_size > MODEL_MAX_TOKENS:
        raise ValueError(f"chunk_size {chunk_size} exceeds embedding model limit {MODEL_MAX_TOKENS}")

    spans = tokenizer.spans(text)
    if not spans:
        return []

    n = len(spans)

    def is_word_start(i: int) -> bool:
        # A cut before token i is clean only if whitespace separates it from
        # token i-1; otherwise we'd split a word ("dec" | "##livity") or detach
        # punctuation from it.
        return i == 0 or i >= n or spans[i][0] > spans[i - 1][1]

    def snap_back(i: int, lo: int) -> int:
        """Move boundary i left to the nearest word start (not below lo)."""
        j = i
        while j > lo and not is_word_start(j) and i - j < MAX_SNAP_TOKENS:
            j -= 1
        return j if is_word_start(j) else i

    windows: list[tuple[int, int]] = []  # token index ranges [start, end)
    start = 0
    while True:
        end = min(start + chunk_size, n)
        if end < n:
            end = snap_back(end, lo=start + 1)
        windows.append((start, end))
        if end == n:
            break
        start = snap_back(max(end - overlap, start + 1), lo=start + 1)
    # Fold a tiny trailing window into its predecessor, as long as the merged
    # window still fits the embedding model.
    if (
        len(windows) > 1
        and windows[-1][1] - windows[-2][1] < overlap
        and windows[-1][1] - windows[-2][0] <= MODEL_MAX_TOKENS
    ):
        last_end = windows.pop()[1]
        windows[-1] = (windows[-1][0], last_end)

    return [Span(spans[a][0], spans[b - 1][1], b - a) for a, b in windows]
