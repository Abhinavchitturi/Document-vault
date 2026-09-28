"""Show where an answer came from: the page, with the passage and the exact facts highlighted.

For a PDF page this returns rectangles (in PDF points, origin top-left) that the
web page draws over a rendered image of the page:
    passage  light highlight: the chunk the answer was built from
    facts    strong highlight: amounts, dates and numbers from the answer that
             appear verbatim on the page ("Rs. 2,486", "18 September 2026")
For Word/text files it returns character spans in the page text instead.

Scans and photos ("scan" kind) have no text layer; their OCR word positions (saved at upload
in files/<doc_id>.ocr.json, see backend/parser/ocr.py) give the same rectangles, in pixels of
the straightened page image served by page_image(). Older uploads are OCR'd on first view.

The original file is never modified.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.parser.extract import IMAGE_SUFFIXES, MIN_PAGE_CHARS, OCR_RENDER_SCALE, extract_pages
from backend.parser.ocr import OcrPage, load_ocr, ocr_available, page_image_from_pdf, read_image, rotate, save_ocr

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
CURRENCY = {"rs", "rs.", "inr", "₹", "usd", "$"}
UNITS = {"sq", "ft", "sqft", "m", "kg", "units", "years", "year", "months", "days", "hours", "mbps", "litres",
         "liters", "ton", "tons", "%", "gb", "kw", "kwh"}


@dataclass
class Highlights:
    kind: str  # "pdf", "scan" (OCR'd page), "text", or "image" (a scan that can't be read: no Tesseract)
    page: int
    pages: int
    width: float = 0.0  # page size: PDF points (pdf) or pixels of the straightened image (scan)
    height: float = 0.0
    passage: list = field(default_factory=list)  # pdf: [x, y, w, h]; text: [start, end]
    facts: list = field(default_factory=list)
    facts_found: list[str] = field(default_factory=list)
    facts_missing: list[str] = field(default_factory=list)
    passage_found: bool = False
    scanned: bool = False
    text: str | None = None  # text kind only
    confidence: int | None = None  # scan kind: how sure OCR was about this page's words, 0-100


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
            return scan_highlights(path, page, len(pdf), passage, answer)
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


# ---------- scans and photos (OCR word boxes) ----------

def _ocr_page(path: Path, page: int) -> OcrPage | None:
    """This page's OCR result: saved at upload, or read now (and saved) for older uploads."""
    saved = load_ocr(path)
    if page in saved:
        return saved[page]
    if not ocr_available():
        return None
    if path.suffix.lower() in IMAGE_SUFFIXES:
        from PIL import Image

        with Image.open(path) as image:
            result = read_image(image)
    else:
        result = read_image(page_image_from_pdf(path, page, OCR_RENDER_SCALE))
    saved[page] = result
    save_ocr(path, saved)
    return result


def _word_rects(words: list, start: int, end: int) -> list[list[float]]:
    """Boxes of the words overlapping text[start:end], merged into one rectangle per line."""
    rects, cur = [], None
    for x, y, w, h, s, e in words:
        if e <= start or s >= end:
            continue
        box = [x, y, x + w, y + h]  # left, top, right, bottom
        overlap = min(box[3], cur[3]) - max(box[1], cur[1]) if cur else 0
        if cur and overlap > 0.4 * min(h, cur[3] - cur[1]) and x >= cur[2] - h:
            cur = [cur[0], min(cur[1], box[1]), max(cur[2], box[2]), max(cur[3], box[3])]
        else:
            if cur:
                rects.append(cur)
            cur = box
    if cur:
        rects.append(cur)
    return [[l - 3, t - 3, r - l + 6, b - t + 6] for l, t, r, b in rects]


def scan_highlights(path: Path, page: int, pages: int, passage: str | None, answer: str | None) -> Highlights:
    ocr = _ocr_page(path, page)
    if ocr is None:  # no Tesseract: show the page without highlights
        kind = "image" if path.suffix.lower() in IMAGE_SUFFIXES else "pdf"
        return Highlights(kind, page, pages, scanned=True)
    result = Highlights("scan", page, pages, ocr.width, ocr.height, scanned=True, confidence=ocr.confidence)
    norm, index = _normalize(ocr.text)

    def to_rects(span):
        s, e = span
        return _word_rects(ocr.words, index[s], index[e - 1] + 1)

    passage_span = _find_passage(norm, passage) if passage else None
    if passage_span:
        result.passage_found = True
        result.passage = to_rects(passage_span)
    spans, result.facts_found, result.facts_missing = _find_facts(norm, answer_facts(answer) if answer else [], passage_span)
    result.facts = [r for span in spans for r in to_rects(span)]
    return result


def _is_scanned_pdf_page(path: Path, page: int) -> bool:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"page {page} doesn't exist (1-{len(pdf)})")
        textpage = pdf[page - 1].get_textpage()
        scanned = len(textpage.get_text_range().strip()) < MIN_PAGE_CHARS
        textpage.close()
        return scanned
    finally:
        pdf.close()


def page_image(path: Path, page: int, max_width_px: int = 1600) -> tuple[bytes, str]:
    """(image bytes, media type) of one page as the viewer shows it. Scans and photos come
    straightened (the way OCR read them) so highlight boxes line up."""
    suffix = path.suffix.lower()
    if suffix == ".pdf" and not _is_scanned_pdf_page(path, page):
        return render_pdf_page(path, page, max_width_px), "image/png"
    if suffix == ".pdf":
        image = page_image_from_pdf(path, page, 2.0)
    elif suffix in IMAGE_SUFFIXES:
        if page != 1:
            raise ValueError(f"page {page} doesn't exist (1-1)")
        from PIL import Image, ImageOps

        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
    else:
        raise ValueError("page images are only available for PDFs, scans and photos")
    saved = load_ocr(path).get(page)
    image = rotate(image.convert("RGB"), saved.rotation if saved else 0)
    if image.width > max_width_px:
        image = image.resize((max_width_px, round(image.height * max_width_px / image.width)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), "image/jpeg"


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
        if page != 1:
            raise ValueError(f"page {page} doesn't exist (1-1)")
        return scan_highlights(path, page, 1, passage, answer)
    return text_highlights(path, page, passage, answer)
