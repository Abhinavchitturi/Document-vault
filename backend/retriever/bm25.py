"""Keyword search (BM25) over the vault's passages.

Vector search understands meaning ("when does my bike cover end" finds "expires on"), but
it is weak at exact strings: a policy number, a serial number, a consumer number, an
amount. BM25 scores passages by the words they share with the question, weighting rare
words (like "0093318") far above common ones, so exact lookups land at the top.

Tokens are lowercase words and numbers, with thousands separators dropped ("2,486" ->
"2486"). Identifiers written with / - . are also kept whole ("kmi/pc/25/0093318"),
so the full number matches exactly while its parts still match partially.

The index lives in memory and is rebuilt when the vault changes (a family vault has
thousands of passages at most; rebuilding takes milliseconds).
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from pathlib import Path

K1, B = 1.5, 0.75

STOPWORDS = set(
    "a an and are as at be by did do does for from had has have how i in is it its me my of on or our "
    "so than that the their them there these this those to was we were what when where which who whom "
    "why will with you your can could should would please tell show find give get much many".split()
)

_NUMBER_COMMAS = re.compile(r"(?<=\d),(?=\d{2,3}\b)")  # 2,486 / 1,00,000 (Indian grouping)
_WORD = re.compile(r"[a-z0-9]+")
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[/\-.][a-z0-9]+)+")


def tokenize(text: str) -> list[str]:
    text = _NUMBER_COMMAS.sub("", text.lower())
    tokens = [_singular(w) for w in _WORD.findall(text) if w not in STOPWORDS]
    tokens += [m for m in _IDENTIFIER.findall(text) if re.search(r"\d", m)]  # whole ids, not "e.g"
    return tokens


def identifiers(query: str) -> list[str]:
    """Tokens that look like a number you'd copy from a document: a policy, serial, account or
    phone number (has a digit and is long, or has / - . inside). Not years or small amounts."""
    return [t for t in dict.fromkeys(tokenize(query))
            if re.search(r"\d", t) and (len(t) >= 5 or re.search(r"[/\-.]", t)) and not re.fullmatch(r"(19|20)\d\d", t)]


def _singular(word: str) -> str:
    """'bills' -> 'bill', 'warranties' -> 'warranty'; leaves 'glass', 'status', numbers alone."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")) and not word[-2].isdigit():
        return word[:-1]
    return word


def passage_tokens(chunk: dict) -> list[str]:
    """What BM25 indexes for a chunk: the file name and type (like the embedded text), then the text."""
    name = Path(chunk["filename"]).stem.replace("_", " ")
    return tokenize(f"{name} {chunk['category']} {chunk['text']}")


class BM25Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.doc_tokens = [Counter(passage_tokens(c)) for c in chunks]
        self.lengths = [sum(t.values()) for t in self.doc_tokens]
        self.avg_len = sum(self.lengths) / len(chunks) if chunks else 0.0
        df = Counter(term for tokens in self.doc_tokens for term in tokens)
        n = len(chunks)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def exact_matches(self, query: str) -> dict[str, int]:
        """chunk_id -> how many of the query's identifiers (see identifiers()) the chunk contains."""
        ids = [t for t in identifiers(query) if t in self.idf]
        if not ids:
            return {}
        return {c["chunk_id"]: n for c, tokens in zip(self.chunks, self.doc_tokens)
                if (n := sum(1 for t in ids if t in tokens))}

    def rank(self, query: str, category: str | None = None) -> list[tuple[dict, float]]:
        """Chunks sharing at least one word with the query, best first."""
        terms = [t for t in dict.fromkeys(tokenize(query)) if t in self.idf]
        results = []
        for chunk, tokens, length in zip(self.chunks, self.doc_tokens, self.lengths):
            if category and chunk["category"] != category:
                continue
            score = 0.0
            for term in terms:
                tf = tokens.get(term)
                if tf:
                    score += self.idf[term] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self.avg_len))
            if score > 0:
                results.append((chunk, score))
        results.sort(key=lambda r: -r[1])
        return results


class KeywordSearch:
    """BM25 over a DocumentStore's chunks, rebuilt whenever the store changes."""

    def __init__(self, store):
        self.store = store
        self._index: BM25Index | None = None
        self._revision = None
        self._lock = threading.Lock()

    def index(self) -> BM25Index:
        with self._lock:
            if self._index is None or self._revision != self.store.revision:
                self._revision = self.store.revision
                self._index = BM25Index(list(self.store.chunks))
            return self._index

    def rank(self, query: str, category: str | None = None) -> list[tuple[dict, float]]:
        return self.index().rank(query, category)

    def exact_matches(self, query: str) -> dict[str, int]:
        return self.index().exact_matches(query)
