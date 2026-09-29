"""Phase 8: sort each uploaded document into a type (insurance, warranty, bill, property, other).

Uses only the models already on this computer, no extra downloads:

    1. Keywords   "premium", "sum insured", "due date", "serial number"... (and the file name)
    2. Meaning    the search model compares the document's opening with a short description
                  of each type, so a policy that never says "insurance" still lands right
    3. Assistant  only when 1 and 2 disagree or are unsure: the local answer model (Ollama)
                  reads the start of the document and picks a type, as structured JSON

Clear-cut documents (most bills and policies) are settled by 1 + 2 in milliseconds; the
assistant is asked about the rest. Every decision keeps a confidence (0-100). Anything
under REVIEW_BELOW is marked "check the type" in Documents; a type chosen by a person is
never changed by the machine again.

Measure it on the sample documents: python -m backend.eval.classify_eval
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from backend.parser.categories import _KEYWORDS, CATEGORIES

logger = logging.getLogger(__name__)

REVIEW_BELOW = 60  # confidence under this: ask the person to check the type

DESCRIPTIONS = {
    "insurance": "An insurance policy or certificate: health, car, bike, life or home insurance, with a policy "
                 "number, premium, sum insured or IDV, policy period, insured persons and nominee, claims.",
    "warranty": "A warranty or guarantee card, or a purchase invoice for an appliance or gadget: product, model and "
                "serial number, date of purchase, dealer, warranty period, service centre.",
    "bill": "A bill, invoice or receipt for a utility or service: electricity, water, gas, internet, mobile; "
            "amount due, due date, billing period, units consumed, account or consumer number.",
    "property": "A property document: sale deed, registration, property tax receipt, khata, flat or land details, "
                "carpet area, survey number, stamp duty.",
    "other": "Some other personal document: an ID card, bank statement, letter, certificate, medical report, "
             "school or office document.",
}

ASSISTANT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["category", "confidence"],
}
ASSISTANT_CONFIDENCE = {"high": 90, "medium": 70, "low": 45}


@dataclass
class Decision:
    category: str
    confidence: int  # 0-100
    method: str  # "keywords+meaning", "assistant" or "keywords" (what settled it)

    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_BELOW


def _softmax(scores: dict[str, float], temperature: float) -> dict[str, float]:
    top = max(scores.values())
    exp = {c: math.exp((s - top) / temperature) for c, s in scores.items()}
    total = sum(exp.values())
    return {c: v / total for c, v in exp.items()}


def keyword_scores(filename: str, text: str) -> dict[str, float]:
    """Share of keyword evidence per type (the file name counts extra). No evidence at all
    leans towards "other"."""
    name, body = filename.lower(), text.lower()[:20_000]
    hits = {c: 0.0 for c in CATEGORIES}
    for category, words in _KEYWORDS.items():
        for w in words:
            hits[category] += 3 * (w in name) + len(re.findall(r"\b" + re.escape(w) + r"\b", body))
    total = sum(hits.values())
    if total == 0:
        return {c: (0.6 if c == "other" else 0.1) for c in CATEGORIES}
    smoothed = {c: (h + 0.25) / (total + 0.25 * len(CATEGORIES)) for c, h in hits.items()}
    return smoothed


class Classifier:
    def __init__(self, embedder=None, llm=None, agree_margin: float = 0.25):
        self.embedder = embedder
        self.llm = llm if llm is not None and hasattr(llm, "generate_json") else None
        self.agree_margin = agree_margin
        self._descriptions: np.ndarray | None = None

    def meaning_scores(self, filename: str, text: str) -> dict[str, float] | None:
        if self.embedder is None:
            return None
        if self._descriptions is None:
            self._descriptions = np.asarray(self.embedder.encode_queries(list(DESCRIPTIONS.values())), dtype=np.float32)
        head = f"{Path(filename).stem.replace('_', ' ')}\n{text[:1500]}"
        doc = np.asarray(self.embedder.encode_passages([head]), dtype=np.float32)[0]
        sims = self._descriptions @ doc
        return _softmax(dict(zip(DESCRIPTIONS, map(float, sims))), temperature=0.03)

    def _ask_assistant(self, filename: str, text: str) -> Decision | None:
        system = ("You sort a family's documents into one type. Types:\n"
                  + "\n".join(f"- {c}: {d}" for c, d in DESCRIPTIONS.items())
                  + "\nAnswer with the type and how confident you are.")
        prompt = f"File name: {filename}\n\nStart of the document:\n{text[:3000]}"
        try:
            answer = self.llm.generate_json(system, prompt, ASSISTANT_SCHEMA)
        except Exception as e:  # Ollama off, model missing, bad JSON: fall back quietly
            logger.info("Assistant couldn't sort %s (%s); using keywords and meaning", filename, e)
            return None
        category = answer.get("category")
        if category not in CATEGORIES:
            return None
        return Decision(category, ASSISTANT_CONFIDENCE.get(answer.get("confidence"), 45), "assistant")

    def classify(self, filename: str, text: str) -> Decision:
        kw = keyword_scores(filename, text)
        meaning = self.meaning_scores(filename, text)
        combined = {c: (kw[c] + meaning[c]) / 2 for c in CATEGORIES} if meaning else kw
        ranked = sorted(combined, key=combined.get, reverse=True)
        best, runner_up = ranked[0], ranked[1]
        margin = combined[best] - combined[runner_up]
        agree = meaning is None or max(kw, key=kw.get) == max(meaning, key=meaning.get)
        confidence = round(100 * min(1.0, combined[best] + margin) if agree else 100 * combined[best])
        quick = Decision(best, max(0, min(100, confidence)), "keywords+meaning" if meaning else "keywords")
        if agree and margin >= self.agree_margin:
            return quick  # clear-cut: no need to wake the assistant
        if self.llm is not None:
            asked = self._ask_assistant(filename, text)
            if asked is not None:
                if asked.category == best:  # both agree: more sure than either alone
                    asked.confidence = max(asked.confidence, quick.confidence)
                return asked
        if not agree:
            # No assistant to settle it: plain keyword evidence ("premium", "due date"...) is precise when
            # present, so it wins over a hunch from meaning; either way, ask the person to check.
            kw_best = max(kw, key=kw.get)
            chosen = kw_best if kw[kw_best] >= 0.5 else best
            return Decision(chosen, min(quick.confidence, REVIEW_BELOW - 1), quick.method)
        return quick
