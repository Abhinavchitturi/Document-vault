"""Retrieval metrics against BEIR qrels.

We retrieve *chunks*, but BEIR's qrels judge *documents*. So a chunk ranking is
first collapsed to a document ranking: each document takes the rank of its best
chunk, and later chunks of the same document are dropped.

Definitions (averaged over every query in the qrels; a query with no results
scores 0):
- Recall@k: fraction of a query's relevant docs that appear in the top k.
- MRR@k:    1 / rank of the first relevant doc in the top k, else 0.
- nDCG@k:   rank-discounted gain, normalized by the ideal ranking. This is the
            headline metric BEIR reports, so it lets us sanity-check against
            published numbers (ours will be higher: 100k-doc subset, not 2.68M).
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def to_doc_ranking(chunk_doc_ids: Iterable[str], scores: Iterable[float], k: int) -> list[tuple[str, float]]:
    """Collapse a best-first chunk ranking into the top-k (doc_id, score) list."""
    seen: set[str] = set()
    ranking = []
    for doc_id, score in zip(chunk_doc_ids, scores):
        if doc_id not in seen:
            seen.add(doc_id)
            ranking.append((doc_id, float(score)))
            if len(ranking) == k:
                break
    return ranking


def _relevant(rels: dict[str, int]) -> set[str]:
    return {d for d, s in rels.items() if s > 0}


def recall_at_k(ranked: Sequence[str], rels: dict[str, int], k: int) -> float:
    relevant = _relevant(rels)
    return len(relevant.intersection(ranked[:k])) / len(relevant) if relevant else 0.0


def mrr_at_k(ranked: Sequence[str], rels: dict[str, int], k: int) -> float:
    relevant = _relevant(rels)
    for i, doc_id in enumerate(ranked[:k]):
        if doc_id in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ranked: Sequence[str], rels: dict[str, int], k: int) -> float:
    dcg = sum(rels.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted((s for s in rels.values() if s > 0), reverse=True)[:k]
    idcg = sum(s / math.log2(i + 2) for i, s in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def evaluate(
    run: dict[str, list[str]],
    qrels: dict[str, dict[str, int]],
    recall_ks: Sequence[int] = (1, 10, 100),
    mrr_k: int = 10,
    ndcg_k: int = 10,
) -> dict[str, float]:
    """Average metrics over all qrels queries. `run` maps query_id -> ranked doc ids."""
    totals: dict[str, float] = {f"recall@{k}": 0.0 for k in recall_ks}
    totals[f"mrr@{mrr_k}"] = 0.0
    totals[f"ndcg@{ndcg_k}"] = 0.0
    for qid, rels in qrels.items():
        ranked = run.get(qid, [])
        for k in recall_ks:
            totals[f"recall@{k}"] += recall_at_k(ranked, rels, k)
        totals[f"mrr@{mrr_k}"] += mrr_at_k(ranked, rels, mrr_k)
        totals[f"ndcg@{ndcg_k}"] += ndcg_at_k(ranked, rels, ndcg_k)
    n = len(qrels)
    return {name: round(total / n, 4) for name, total in totals.items()}
