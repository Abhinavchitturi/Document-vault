"""Phase 9: read each document's key facts, for search results, reminders (Phase 10) and a
"coming up" list:

    expires_on  when a policy, cover or warranty ends          (YYYY-MM-DD)
    due_on      when a bill must be paid                        (YYYY-MM-DD)
    amount      the main amount: amount due, total premium, tax paid
    reference   the main number: policy, account, serial, receipt or registration number
    issuer      the company or office that issued it

Readers, best first, picked automatically:
    needle   Needle 3 (Cactus Compute, Apache-2.0): a 35 MB extraction model that runs on this
             computer. `pip install cactus-needle`; its files are downloaded once, then it runs
             with the internet off. Its anonymous usage counts are switched off here.
    ollama   the local answer model, asked for the same fields as JSON
    rules    plain text patterns ("Due date: 18 September 2026"), always available

Whatever reads them, every value is checked against the document: a date or number that
doesn't appear in the text is dropped rather than trusted. Measure the readers on the
samples: python -m backend.eval.facts_eval
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import date

logger = logging.getLogger(__name__)

FIELDS = ("expires_on", "due_on", "amount", "reference", "issuer")

DESCRIPTIONS = {
    "issuer": "The company, insurer, utility or government office that issued the document.",
    "reference": "The main identifying number: policy number, consumer or account number, serial number, "
                 "receipt number or registration number. Copy it exactly.",
    "expires_on": "The date the policy, cover or warranty ends: the end of the policy period, 'valid until', "
                  "'covered until'. Not the purchase or start date. As YYYY-MM-DD.",
    "due_on": "The date by which a bill must be paid (due date). As YYYY-MM-DD.",
    "amount": "The main amount in rupees: total amount due or payable, total premium, or total tax paid. "
              "Digits only, like 2486 or 1355.82.",
}

SCHEMA = {  # one record, used as Needle's only "tool" and as Ollama's JSON format
    "name": "record_document_facts",
    "description": "Record the key facts stated in a family document (bill, insurance policy, warranty, property paper).",
    "parameters": {
        "type": "object",
        "properties": {
            "issuer": {"type": "string", "description": DESCRIPTIONS["issuer"]},
            "reference": {"type": "string", "description": DESCRIPTIONS["reference"]},
            "expires_on": {"type": "string", "format": "date", "description": DESCRIPTIONS["expires_on"]},
            "due_on": {"type": "string", "format": "date", "description": DESCRIPTIONS["due_on"]},
            "amount": {"type": "number", "description": DESCRIPTIONS["amount"]},
        },
        "required": [],
    },
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
_DATE = re.compile(r"\b(\d{1,2})[\s\-./]+([A-Za-z]{3,9})[\s\-.,/]+(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
_ORG = re.compile(r"(?:\bco\.|\b(?:ltd|limited|pvt|company|corporation|department|insurance|electronics|appliances|"
                  r"broadband|systems|bank|authority|board|services|india)\b)", re.I)
_AMOUNT = re.compile(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)


@dataclass
class Facts:
    expires_on: str | None = None
    due_on: str | None = None
    amount: str | None = None
    reference: str | None = None
    issuer: str | None = None
    method: str = "none"

    def as_dict(self) -> dict:
        return asdict(self)

    def as_document_fields(self) -> dict:
        """As stored on the document record (see backend/vector_store/store.py)."""
        d = asdict(self)
        d["facts_method"] = d.pop("method")
        return d


# ---------- reading dates and amounts in text ----------

def dates_in(text: str) -> list[tuple[int, date]]:
    """(position, date) for every date written in the text: '18 September 2026', '2026-09-18', '18/09/2026'."""
    found = []
    for m in _DATE.finditer(text):
        try:
            if m.group(1):
                month = MONTHS.get(m.group(2).lower()) or MONTHS.get(m.group(2).lower()[:3])
                if not month:
                    continue
                found.append((m.start(), date(int(m.group(3)), month, int(m.group(1)))))
            elif m.group(4):
                found.append((m.start(), date(int(m.group(4)), int(m.group(5)), int(m.group(6)))))
            else:  # Indian order: day/month/year
                found.append((m.start(), date(int(m.group(9)), int(m.group(8)), int(m.group(7)))))
        except ValueError:
            continue
    return found


def _digits(value) -> str:
    return re.sub(r"[^\d.]", "", str(value)).rstrip("0").rstrip(".") if "." in str(value) else re.sub(r"\D", "", str(value))


def grounded(facts: Facts, text: str) -> Facts:
    """Keep only values the document actually states (dates as written in any format, amounts,
    numbers and names as text)."""
    written = {d.isoformat() for _, d in dates_in(text)}
    for field in ("expires_on", "due_on"):
        value = getattr(facts, field)
        if value and str(value)[:10] not in written:
            setattr(facts, field, None)
        elif value:
            setattr(facts, field, str(value)[:10])
    if facts.amount is not None:
        amounts = {_digits(a) for a in _AMOUNT.findall(text)} | {_digits(a) for a in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
        wanted = _digits(facts.amount)
        facts.amount = wanted if wanted and wanted in amounts else None
    squashed = re.sub(r"\s+", "", text).lower()
    for field in ("reference", "issuer"):
        value = getattr(facts, field)
        if value and re.sub(r"\s+", "", str(value)).lower() not in squashed:
            setattr(facts, field, None)
    return facts


# ---------- readers ----------

class RulesReader:
    """Text patterns: labelled lines like 'Due date: …', 'Policy Number: …', a period 'X to Y'."""

    name = "rules"

    def read(self, text: str) -> Facts:
        facts = Facts(method=self.name)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            ds = dates_in(line)
            if not ds and re.search(r"period|valid|covered|expir|until|due date", low) and i + 1 < len(lines):
                # "### Period of Insurance" followed by the dates, or a sentence wrapped onto the next line
                ds = dates_in(lines[i + 1]) or dates_in(line + " " + lines[i + 1])
            if facts.due_on is None and "due date" in low and ds:
                facts.due_on = ds[-1][1].isoformat()
            if facts.expires_on is None and ds and re.search(r"expir|valid until|valid till|covered until|until|period|to midnight", low) \
                    and not re.search(r"billing|bill period|billing cycle", low + (lines[i - 1].lower() if i else "")):  # a bill's period isn't an expiry
                facts.expires_on = max(d for _, d in ds).isoformat()
            if facts.amount is None and re.search(r"total|amount (due|payable)|premium|tax paid", low):
                m = _AMOUNT.search(line)
                if m and not re.search(r"own damage|third party|net premium|charges:", low):
                    facts.amount = _digits(m.group(1))
            if facts.reference is None:
                m = re.search(r"(policy|consumer|account|connection|receipt|registration|serial)\s*(number|no\.?|id)\s*:\s*(\S+)", line, re.I)
                if m:
                    facts.reference = m.group(3).strip(".,;")
            if facts.issuer is None and i < 8 and _ORG.search(line) and not re.search(r"sample document", low):
                facts.issuer = re.sub(r"^#+\s*|\s*\(fictional\)\s*$", "", line).strip()
        # A later "Total …" line (the grand total) beats an earlier subtotal.
        totals = [(_AMOUNT.search(l), l.lower()) for l in lines if re.search(r"\btotal\b", l, re.I)]
        totals = [(m, l) for m, l in totals if m]
        if totals:
            facts.amount = _digits(totals[-1][0].group(1))
        return facts


class OllamaReader:
    name = "ollama"

    def __init__(self, llm):
        self.llm = llm

    def read(self, text: str) -> Facts:
        system = ("You read one family document and fill in its key facts. Only copy what the document states; "
                  "leave a field out when it isn't there.\n"
                  + "\n".join(f"- {k}: {v}" for k, v in DESCRIPTIONS.items()))
        answer = self.llm.generate_json(system, f"Document:\n{text[:6000]}", SCHEMA["parameters"])
        return Facts(**{k: answer.get(k) for k in FIELDS}, method=self.name)


class NeedleReader:
    """Needle 3 through the official `cactus-needle` package (structured extraction mode)."""

    name = "needle"

    def __init__(self):
        os.environ["NEEDLE_TELEMETRY"] = "0"  # no usage counts leave this computer
        os.environ["DO_NOT_TRACK"] = "1"
        import needle  # noqa: F401  (ImportError if not installed)

        self._needle = needle
        self.unavailable: str | None = None  # set after a failure to load: not retried every upload

    def read(self, text: str) -> Facts:
        if self.unavailable:
            raise RuntimeError(self.unavailable)
        try:
            answer = self._needle.extract(snippets(text), SCHEMA, strict=False) or {}
        except Exception as e:  # files not downloaded, engine refused: the next reader takes over
            if not isinstance(e, getattr(self._needle, "ExtractionValidationError", ())):
                self.unavailable = (f"Needle 3 isn't ready ({type(e).__name__}). Download its files once with: "
                                    "python -m backend.extract.needle_setup")
                logger.warning(self.unavailable)
            raise RuntimeError(f"Needle couldn't read this document: {e}") from e
        return Facts(**{k: answer.get(k) for k in FIELDS}, method=self.name)


def snippets(text: str, limit: int = 3000) -> str:
    """Needle reads a short window: the heading lines plus the lines that carry the facts."""
    lines = [l for l in text.splitlines() if l.strip()]
    keep = lines[:6] + [l for l in lines[6:] if re.search(
        r"expir|valid|until|period|due|total|amount|premium|paid|number|no\.|serial|receipt|account|\d{4}", l, re.I)]
    out, size = [], 0
    for l in keep:
        if size + len(l) > limit:
            break
        out.append(l)
        size += len(l) + 1
    return "\n".join(out)


class FactsReader:
    """Tries the best available reader, falls back to the next one, and grounds the result."""

    def __init__(self, llm=None, prefer: str | None = None):
        prefer = prefer or os.environ.get("VAULT_FACTS_READER", "auto")  # auto | needle | ollama | rules
        self.readers = []
        if prefer in ("auto", "needle"):
            try:
                self.readers.append(NeedleReader())
            except ImportError:
                if prefer == "needle":
                    raise
                logger.info("Needle 3 isn't installed (pip install cactus-needle); reading facts with %s",
                            "the local answer model" if llm is not None else "text patterns")
        if prefer in ("auto", "ollama") and llm is not None and hasattr(llm, "generate_json"):
            self.readers.append(OllamaReader(llm))
        self.readers.append(RulesReader())

    @property
    def name(self) -> str:
        """The reader in use: the first one that hasn't failed to load."""
        return next(r.name for r in self.readers if not getattr(r, "unavailable", None))

    def read(self, text: str) -> Facts:
        rules = grounded(RulesReader().read(text), text)
        for reader in self.readers:
            try:
                facts = grounded(reader.read(text), text)
            except Exception as e:
                logger.info("%s couldn't read facts (%s); trying the next reader", reader.name, e)
                continue
            # A model that missed a field (or stated one the text doesn't have) is topped up by the patterns.
            for field in FIELDS:
                if getattr(facts, field) is None:
                    setattr(facts, field, getattr(rules, field))
            return facts
        return rules


_shared: FactsReader | None = None


def default_reader() -> FactsReader:
    """One reader for a whole import (so a Needle that isn't set up is tried once, not per file)."""
    global _shared
    if _shared is None:
        _shared = FactsReader()
    return _shared
