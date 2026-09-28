"""Load a BEIR dataset (corpus, queries, qrels) and select a corpus subset.

The full NQ corpus is ~2.68M Wikipedia passages, which is more than we need
while iterating. `select_subset` keeps every passage that is relevant to some
query in the qrels (so retrieval eval stays meaningful) and fills the rest of
the budget with a random sample of distractor passages.
"""

from __future__ import annotations

import csv
import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import ftfy

logger = logging.getLogger(__name__)


@dataclass
class Document:
    doc_id: str
    title: str
    text: str


def iter_corpus(dataset_dir: str | Path) -> Iterator[Document]:
    """Stream documents from corpus.jsonl without loading it all into memory."""
    with open(Path(dataset_dir) / "corpus.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            yield Document(doc_id=str(row["_id"]), title=row.get("title", ""), text=row["text"])


def load_queries(dataset_dir: str | Path) -> dict[str, str]:
    queries = {}
    with open(Path(dataset_dir) / "queries.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            queries[str(row["_id"])] = row["text"]
    return queries


def load_qrels(dataset_dir: str | Path, split: str = "test") -> dict[str, dict[str, int]]:
    """Returns {query_id: {doc_id: relevance}}."""
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    with open(Path(dataset_dir) / "qrels" / f"{split}.tsv", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader)  # header: query-id, corpus-id, score
        for query_id, doc_id, score in reader:
            qrels[query_id][doc_id] = int(score)
    return dict(qrels)


def select_subset(
    dataset_dir: str | Path,
    qrels: dict[str, dict[str, int]],
    max_docs: int | None,
    seed: int = 42,
) -> list[Document]:
    """Return all qrels-relevant docs plus random distractors, up to `max_docs` total.

    With `max_docs=None` the whole corpus is returned. Distractors are drawn by
    reservoir sampling in a single pass, so the corpus is never fully in memory.
    """
    if max_docs is None:
        return list(iter_corpus(dataset_dir))

    relevant_ids = {doc_id for rels in qrels.values() for doc_id, score in rels.items() if score > 0}
    if len(relevant_ids) > max_docs:
        raise ValueError(
            f"max_docs={max_docs} is smaller than the {len(relevant_ids)} qrels-relevant docs; "
            "raise max_docs so every query keeps its gold passages"
        )
    n_distractors = max_docs - len(relevant_ids)

    rng = random.Random(seed)
    relevant: list[Document] = []
    reservoir: list[Document] = []
    seen_distractors = 0
    for doc in iter_corpus(dataset_dir):
        if doc.doc_id in relevant_ids:
            relevant.append(doc)
            continue
        seen_distractors += 1
        if len(reservoir) < n_distractors:
            reservoir.append(doc)
        else:
            j = rng.randrange(seen_distractors)
            if j < n_distractors:
                reservoir[j] = doc

    missing = len(relevant_ids) - len(relevant)
    if missing:
        logger.warning("%d qrels-relevant doc ids were not found in the corpus", missing)
    logger.info("Subset: %d relevant + %d distractor docs", len(relevant), len(reservoir))
    return relevant + reservoir


def fix_encoding(docs: Iterable[Document]) -> list[Document]:
    """Repair mojibake in the BEIR NQ source text (e.g. "â€“" -> "–").

    Some NQ passages were double-encoded upstream. Left alone, those byte
    garbles become junk tokens for both BM25 and the embedding model.
    """
    return [Document(d.doc_id, ftfy.fix_text(d.title), ftfy.fix_text(d.text)) for d in docs]
