"""Show where an answer came from: the page, with the passage and the exact facts highlighted.

For a PDF page this returns rectangles (in PDF points, origin top-left) that the
web page draws over a rendered image of the page:
    passage  light highlight: the chunk the answer was built from
    facts    strong highlight: amounts, dates and numbers from the answer that
             appear verbatim on the page ("Rs. 2,486", "18 September 2026")
For Word/text files it returns character spans in the page text instead.

The original file is never modified. Scanned pages have no text layer, so they
are shown without highlights (OCR word positions can add that later).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.parser.extract import IMAGE_SUFFIXES, MIN_PAGE_CHARS, extract_pages

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
CURRENCY = {"rs", "rs.", "inr", "₹", "usd", "$"}
UNITS = {"sq", "ft", "sqft", "m", "kg", "units", "years", "year", "months", "days", "hours", "mbps", "litres",
         "liters", "ton", "tons", "%", "gb", "kw", "kwh"}


@dataclass
class Highlights:
    kind: str  # "pdf", "text" or "image"
    page: int
    pages: int
    width: float = 0.0  # page size in PDF points (pdf only)
    height: float = 0.0
    passage: list = field(default_factory=list)  # pdf: [x, y, w, h]; text: [start, end]
    facts: list = field(default_factory=list)
    facts_found: list[str] = field(default_factory=list)
    facts_missing: list[str] = field(default_factory=list)
    passage_found: bool = False
    scanned: bool = False
    text: str | None = None  # text kind only


# ---------- which facts does the answer state? ----------

def answer_facts(answer: str) -> list[str]:
    """Phrases worth pinpointing on the page: runs of words containing digits, together with
    month names, currency and units around them. 'Rs. 2,486', '18 September 2026', 'SGI/TW/2026/448812'."""
    text = re.sub(r"\[\d+\]", " ", answer).replace("**", " ")
    words = text.split()

    def core(w: str) -> str:
        return w.strip(".,;:!?()\"'").lower()

    has_digit = [bool(re.search(r"\d", w)) for w in words]
    keep = list(has_digit)
    for i, w in enumerate(words):
        c = core(w)
        near_digit = (i > 0 and has_digit[i - 1]) or (i + 1 < len(words) and has_digit[i + 1])
        if not has_digit[i] and near_digit and (c[:3] in MONTHS and c.isalpha() or c in CURRENCY or w.lower().rstrip(".,") in CURRENCY):
            keep[i] = True
        if not has_digit[i] and i > 0 and keep[i - 1] and c in UNITS:
            keep[i] = True
    phrases, current = [], []
    for w, k in zip(words, keep):
        if k:
            current.append(w)
            # a comma or full stop after a word ends the phrase ("2027, 5 days"), except "Rs."
            if w[-1] in ",;:.!?" and w.lower() not in CURRENCY:
                phrases.append(" ".join(current)); current = []
        elif current:
            phrases.append(" ".join(current)); current = []
    if current:
        phrases.append(" ".join(current))
    facts = []
    for p in phrases:
        p = p.strip(".,;:!?()\"'")
        digits = re.sub(r"\D", "", p)
        if len(digits) < 3 and not re.search(r"[A-Za-z]", p.replace("Rs", "")):
            continue  # "1", "25" on their own match too much
        if re.fullmatch(r"(19|20)\d\d", p):
            continue  # a bare year matches all over the page
        if p not in facts:
            facts.append(p)
    return facts


# ---------- matching on normalized text ----------

def _normalize(raw: str) -> tuple[str, list[int]]:
    """Lowercase, collapse whitespace; also return each normalized char's index in `raw`."""
    out, index, prev_space = [], [], True
    for i, ch in enumerate(raw):
        if ch.isspace():
            if not prev_space:
                out.append(" "); index.append(i); prev_space = True
        else:
            out.append(ch.lower()); index.append(i); prev_space = False
    return "".join(out), index


def _norm(s: str) -> str:
    return _normalize(s)[0].strip()


def _find_passage(norm_page: str, passage: str) -> tuple[int, int] | None:
    """Span of the passage in the normalized page; falls back to matching its start and end
    (text cleanup can change a few characters in between)."""
    p = _norm(passage)
    if not p:
        return None
    at = norm_page.find(p)
    if at >= 0:
        return at, at + len(p)
    head, tail = p[:60], p[-60:]
    start = norm_page.find(head)
    end = norm_page.find(tail, max(start, 0))
    if start >= 0 and end >= 0:
        return start, end + len(tail)
    return None


def _find_facts(norm_page: str, facts: list[str], within: tuple[int, int] | None) -> tuple[list, list[str], list[str]]:
    spans, found, missing = [], [], []
    for fact in facts:
        f = _norm(fact)
        hits = [m.start() for m in re.finditer(re.escape(f), norm_page)]
        if within:
            inside = [h for h in hits if within[0] <= h < within[1]]
            hits = inside or hits
        if hits:
            found.append(fact)
            spans.extend((h, h + len(f)) for h in hits)
        else:
            missing.append(fact)
    return spans, found, missing


# ---------- PDF ----------

def _page_chars(textpage) -> str:
    raw = textpage.get_text_range()
    if len(raw) == textpage.count_chars():
        return raw
    # Rare PDFs (e.g. characters outside the basic plane) don't line up; read char by char.
    return "".join(textpage.get_text_range(i, 1) or " " for i in range(textpage.count_chars()))


def _rects(textpage, raw: str, start: int, end: int, page_height: float) -> list[list[float]]:
    """Merge character boxes from raw[start:end] into one rectangle per line."""
    rects, cur = [], None
    for i in range(start, min(end, len(raw))):
        if raw[i].isspace():
            continue
        left, bottom, right, top = textpage.get_charbox(i)
        if right - left <= 0 or top - bottom <= 0:
            continue
        overlap = min(top, cur[3]) - max(bottom, cur[1]) if cur else 0
        same_line = cur and overlap > 0.3 * min(top - bottom, cur[3] - cur[1]) and left >= cur[2] - 2
        if same_line:
            cur = [cur[0], min(cur[1], bottom), max(cur[2], right), max(cur[3], top)]
        else:
            if cur:
                rects.append(cur)
            cur = [left, bottom, right, top]
    if cur:
        rects.append(cur)
    pad = 1.5
    return [[round(l - pad, 1), round(page_height - t - pad, 1), round(r - l + 2 * pad, 1), round(t - b + 2 * pad, 1)]
            for l, b, r, t in rects]


def pdf_highlights(path: Path, page: int, passage: str | None, answer: str | None) -> Highlights:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"page {page} doesn't exist (1-{len(pdf)})")
        pg = pdf[page - 1]
        width, height = pg.get_size()
        tp = pg.get_textpage()
        raw = _page_chars(tp)
        result = Highlights("pdf", page, len(pdf), round(width, 1), round(height, 1))
        if len(raw.strip()) < MIN_PAGE_CHARS:
            result.scanned = True
            return result
        norm, index = _normalize(raw)

        def to_rects(span):
            s, e = span
            return _rects(tp, raw, index[s], index[e - 1] + 1, height)

        passage_span = _find_passage(norm, passage) if passage else None
        if passage_span:
            result.passage_found = True
            result.passage = to_rects(passage_span)
        facts = answer_facts(answer) if answer else []
        spans, result.facts_found, result.facts_missing = _find_facts(norm, facts, passage_span)
        result.facts = [r for span in spans for r in to_rects(span)]
        return result
    finally:
        pdf.close()


def render_pdf_page(path: Path, page: int, max_width_px: int = 1600) -> bytes:
    """PNG of one page, about 2x scale for sharp text on high-resolution screens."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"page {page} doesn't exist (1-{len(pdf)})")
        pg = pdf[page - 1]
        scale = min(2.0, max_width_px / pg.get_size()[0])
        image = pg.render(scale=scale).to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        pdf.close()


# ---------- Word / text / images ----------

def text_highlights(path: Path, page: int, passage: str | None, answer: str | None) -> Highlights:
    pages = extract_pages(path)
    if not 1 <= page <= len(pages):
        raise ValueError(f"page {page} doesn't exist (1-{len(pages)})")
    text = pages[page - 1].text
    norm, index = _normalize(text)
    result = Highlights("text", page, len(pages), text=text)

    def to_span(span):
        s, e = span
        return [index[s], index[e - 1] + 1]

    passage_span = _find_passage(norm, passage) if passage else None
    if passage_span:
        result.passage_found = True
        result.passage = [to_span(passage_span)]
    spans, result.facts_found, result.facts_missing = _find_facts(norm, answer_facts(answer) if answer else [], passage_span)
    result.facts = sorted(to_span(s) for s in spans)
    return result


def highlights_for(path: Path, page: int, passage: str | None, answer: str | None) -> Highlights:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_highlights(path, page, passage, answer)
    if suffix in IMAGE_SUFFIXES:
        return Highlights("image", page, 1, scanned=True)
    return text_highlights(path, page, passage, answer)
