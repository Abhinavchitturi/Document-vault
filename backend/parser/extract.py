"""Pull text out of uploaded documents, one entry per page.

Supported:
    .pdf                    text layer via pdfium; scanned pages (no text layer) are OCR'd
    .docx                   paragraphs and tables
    .txt / .md              read as-is
    .png .jpg .jpeg .webp .tif .tiff    OCR (photos or scans of warranty cards, bills...)

OCR (backend/parser/ocr.py) uses Tesseract, a separate program: `brew install tesseract`
on macOS. Without it, text-based PDFs, Word and text files still work; images and scanned
pages are reported as needing OCR. OCR'd pages keep their word positions and a confidence
score (Page.ocr_data) so answers can be highlighted on scans and blurry photos flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import ftfy

from backend.parser.ocr import OcrPage, OcrUnavailableError, ocr_available, read_image, require_ocr

TEXT_SUFFIXES = {".txt", ".md"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
SUPPORTED_SUFFIXES = {".pdf", ".docx"} | TEXT_SUFFIXES | IMAGE_SUFFIXES

MIN_PAGE_CHARS = 25  # a PDF page with less text than this is treated as a scan
OCR_RENDER_SCALE = 3  # render scanned PDF pages at 216 dpi before OCR


class UnsupportedFileError(ValueError):
    pass


@dataclass
class Page:
    number: int  # 1-based
    text: str
    ocr: bool = False
    ocr_data: OcrPage | None = None  # word boxes and confidence, for OCR'd pages


def clean_text(text: str) -> str:
    """Repair mis-encoded characters and normalize whitespace, keeping line breaks."""
    text = ftfy.fix_text(text.replace("\x00", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_pages(path: Path) -> list[Page]:
    import pypdfium2 as pdfium

    pages = []
    pdf = pdfium.PdfDocument(path)
    try:
        for i, page in enumerate(pdf, start=1):
            textpage = page.get_textpage()
            text = clean_text(textpage.get_text_range())
            textpage.close()
            data = None
            if len(text) < MIN_PAGE_CHARS and ocr_available():
                data = read_image(page.render(scale=OCR_RENDER_SCALE).to_pil())
                text = clean_text(data.text)
            page.close()
            pages.append(Page(i, text, data is not None, data))
    finally:
        pdf.close()
    if not any(p.text for p in pages) and not ocr_available():
        raise OcrUnavailableError(
            "This PDF has no text layer (it's a scan). Install Tesseract (macOS: brew install tesseract) "
            "and restart the app."
        )
    return pages


def _docx_pages(path: Path) -> list[Page]:
    import docx

    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return [Page(1, clean_text("\n".join(parts)))]


def _image_pages(path: Path) -> list[Page]:
    from PIL import Image

    require_ocr()  # say what to install before failing on anything else
    with Image.open(path) as image:
        data = read_image(image)
    return [Page(1, clean_text(data.text), ocr=True, ocr_data=data)]


def extract_pages(path: str | Path) -> list[Page]:
    """Return the text of each page. Raises UnsupportedFileError / OcrUnavailableError."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_pages(path)
    if suffix == ".docx":
        return _docx_pages(path)
    if suffix in TEXT_SUFFIXES:
        return [Page(1, clean_text(path.read_text(encoding="utf-8", errors="replace")))]
    if suffix in IMAGE_SUFFIXES:
        return _image_pages(path)
    raise UnsupportedFileError(
        f"Unsupported file type {suffix or '(none)'}. Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )
