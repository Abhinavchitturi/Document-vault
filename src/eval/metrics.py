"""Retrieval metrics for a question set with known answers.

Each question lists its relevant document(s) and a short answer string. Search
returns one result per document (its best-matching chunk), best first.

- Recall@k:  fraction of the question's relevant documents in the top k.
- MRR@k:     1 / rank of the first relevant document in the top k, else 0.
             Rewards putting the right document first, not just somewhere near the top.
- Answer@k:  1 if the answer string appears in the text of any top-k result, else 0.
             Stricter than Recall: the right document can be found through the
             wrong chunk (e.g. its claims page instead of the page with the date),
             and then an LLM reading the chunks still can't answer.
"""

from __future__ import annotations

from typing import Sequence


def recall_at_k(ranked_docs: Sequence[str], relevant: set[str], k: int) -> float:
    return len(relevant.intersection(ranked_docs[:k])) / len(relevant) if relevant else 0.0


def mrr_at_k(ranked_docs: Sequence[str], relevant: set[str], k: int) -> float:
    for i, doc in enumerate(ranked_docs[:k]):
        if doc in relevant:
            return 1.0 / (i + 1)
    return 0.0


def answer_at_k(ranked_texts: Sequence[str], answer: str, k: int) -> float:
    needle = " ".join(answer.lower().split())
    return float(any(needle in " ".join(t.lower().split()) for t in ranked_texts[:k]))
