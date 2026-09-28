"""Document categories, and a keyword-based guess for uploads marked "auto".

The guess only picks a label for browsing and filtering; search never depends
on it, so a wrong guess is harmless and can be fixed by re-uploading with an
explicit category.
"""

from __future__ import annotations

import re

CATEGORIES = ["insurance", "warranty", "bill", "property", "other"]

_KEYWORDS = {
    "insurance": ["insurance", "policy", "insured", "premium", "sum insured", "idv", "claim", "nominee", "policyholder"],
    "warranty": ["warranty", "guarantee", "serial number", "serial no", "model no", "service centre", "service center",
                 "date of purchase", "compressor"],
    "bill": ["bill", "invoice", "amount due", "due date", "units consumed", "billing period", "account number",
             "consumer number", "late fee", "gst"],
    "property": ["sale deed", "property", "registration", "sub-registrar", "khata", "carpet area", "flat no",
                 "property tax", "survey no", "stamp duty"],
}


def guess_category(filename: str, text: str) -> str:
    """Score each category by keyword hits; the filename counts extra."""
    name = filename.lower()
    body = text.lower()[:20_000]
    scores = {}
    for category, words in _KEYWORDS.items():
        scores[category] = sum(
            3 * (w in name) + len(re.findall(r"\b" + re.escape(w) + r"\b", body)) for w in words
        )
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"
