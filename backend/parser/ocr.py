"""OCR for photos and scanned pages: straighten, clean up, read, and remember where each word is.

    1. Orientation  phone photos carry an EXIF rotation; scans are often sideways or upside down.
                    Both are fixed before reading (Tesseract's orientation detection, "OSD").
    2. Clean-up     grayscale, stretch the contrast (faded thermal receipts, grey photos), and
                    enlarge small images so letters are big enough to read.
    3. Read         Tesseract returns every word with its box and a confidence (0-100).

Languages: English, plus Hindi when its Tesseract data is installed (`brew install tesseract-lang`
on macOS). PAN and Aadhaar cards are half Hindi; read as English only, those lines turn into
gibberish that also drags the confidence down. VAULT_OCR_LANGS overrides (e.g. "eng+hin+mar").

The confidence counts real words only (two or more letters or digits), not the specks a logo,
emblem, signature or photo leaves behind, so a clean card isn't flagged as hard to read.

The result keeps the word boxes (in the coordinates of the straightened page) so the viewer
can highlight the answer on a scan exactly like on a normal PDF, and a confidence score so
the app can warn when a photo is too blurry to trust.

Tesseract is a separate program: `brew install tesseract` on macOS (it includes English and
orientation detection).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ftfy

logger = logging.getLogger(__name__)

MIN_OCR_WIDTH = 1600  # smaller images are enlarged before reading
RETRY_WIDTH = 2400  # an unclear small image is read once more at this size
MIN_ORIENTATION_CONFIDENCE = 2.0  # below this Tesseract is guessing; leave the page as it is
LOW_CONFIDENCE = 70  # pages read with less confidence than this get a "check the original" warning


class OcrUnavailableError(RuntimeError):
    pass


@dataclass
class OcrPage:
    text: str
    width: int  # size of the straightened page; word boxes use these coordinates
    height: int
    rotation: int = 0  # degrees clockwise the original was turned to read it (0, 90, 180, 270)
    confidence: int | None = None  # average word confidence 0-100; None when no words were found
    words: list = field(default_factory=list)  # [x, y, w, h, start, end]: box and span in `text`

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OcrPage":
        return cls(**{k: d[k] for k in ("text", "width", "height", "rotation", "confidence", "words") if k in d})


def ocr_sidecar(path: Path) -> Path:
    """Where a stored file's OCR results are kept: files/<doc_id>.ocr.json next to files/<doc_id>.pdf."""
    return path.with_name(path.stem + ".ocr.json")


def save_ocr(path: Path, pages: dict[int, OcrPage]) -> None:
    sidecar = ocr_sidecar(path)
    tmp = sidecar.with_suffix(".tmp")
    tmp.write_text(json.dumps({str(n): p.to_dict() for n, p in sorted(pages.items())}), encoding="utf-8")
    tmp.replace(sidecar)


def load_ocr(path: Path) -> dict[int, OcrPage]:
    try:
        raw = json.loads(ocr_sidecar(path).read_text(encoding="utf-8"))
        return {int(n): OcrPage.from_dict(d) for n, d in raw.items()}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


_langs: str | None = None


def ocr_languages() -> str:
    """"eng+hin" when Hindi is installed, else "eng" (or VAULT_OCR_LANGS)."""
    global _langs
    if _langs is None:
        import os

        wanted = os.environ.get("VAULT_OCR_LANGS")
        if wanted:
            _langs = wanted
        else:
            try:
                import pytesseract

                installed = set(pytesseract.get_languages(config=""))
            except Exception:
                installed = {"eng"}
            _langs = "+".join(lang for lang in ("eng", "hin") if lang in installed) or "eng"
    return _langs


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def require_ocr(what: str = "This file") -> None:
    if not ocr_available():
        raise OcrUnavailableError(
            f"{what} needs OCR (it's an image or a scan). Install Tesseract (macOS: brew install tesseract) "
            "and restart the app."
        )


def upright(image):
    """The image turned the way a person would read it: EXIF rotation, then detected orientation."""
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image).convert("RGB")
    return image, _detect_rotation(image)


def rotate(image, rotation: int):
    return image.rotate(-rotation, expand=True) if rotation else image


def _detect_rotation(image) -> int:
    import pytesseract

    small = image.convert("L")
    if small.width > 2000:  # orientation doesn't need full resolution
        small = small.resize((2000, round(small.height * 2000 / small.width)))
    try:
        osd = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
    except Exception:  # too little text to tell, or no orientation data installed
        return 0
    if osd.get("orientation_conf", 0) < MIN_ORIENTATION_CONFIDENCE:
        return 0
    return int(osd.get("rotate", 0)) % 360


def _cleaned(image, width: int = MIN_OCR_WIDTH):
    """Grayscale, contrast stretched, enlarged if narrower than `width`. Returns (image, scale factor)."""
    from PIL import Image, ImageOps

    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    scale = 1.0
    if gray.width < width:
        scale = width / gray.width
        gray = gray.resize((width, round(gray.height * scale)), Image.LANCZOS)
    return gray, scale


def _is_word(token: str) -> bool:
    return sum(ch.isalnum() for ch in token) >= 2


def read_image(image, rotation: int | None = None) -> OcrPage:
    """OCR one page image. `rotation` skips orientation detection when it's already known."""
    require_ocr()
    import pytesseract

    if rotation is None:
        image, rotation = upright(image)
    else:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image).convert("RGB")
    image = rotate(image, rotation)
    page = _read(image, rotation, MIN_OCR_WIDTH)
    if (page.confidence is None or page.confidence < LOW_CONFIDENCE) and image.width < RETRY_WIDTH:
        bigger = _read(image, rotation, RETRY_WIDTH)  # small print (ID cards, receipts) reads better larger
        if (bigger.confidence or 0) > (page.confidence or 0):
            page = bigger
    return page


def _read(image, rotation: int, width: int) -> OcrPage:
    import pytesseract

    cleaned, scale = _cleaned(image, width)
    data = pytesseract.image_to_data(cleaned, lang=ocr_languages(), output_type=pytesseract.Output.DICT)

    text, words, confs = "", [], []
    last_line = last_block = None
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        block = (data["block_num"][i], data["par_num"][i])
        line = (*block, data["line_num"][i])
        if last_line is not None:
            text += "\n\n" if block != last_block else "\n" if line != last_line else " "
        last_line, last_block = line, block
        word = ftfy.fix_text(word)
        start = len(text)
        text += word
        box = [round(data[k][i] / scale) for k in ("left", "top", "width", "height")]
        words.append([*box, start, len(text)])
        if _is_word(word):  # specks from logos and photos don't count against the page
            confs.append((conf, len(word)))
    total = sum(n for _, n in confs)
    confidence = round(sum(c * n for c, n in confs) / total) if total else None
    return OcrPage(text, image.width, image.height, rotation, confidence, words)


def page_image_from_pdf(path, page: int, scale: float):
    """One PDF page rendered as an image (1-based page)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"page {page} doesn't exist (1-{len(pdf)})")
        return pdf[page - 1].render(scale=scale).to_pil()
    finally:
        pdf.close()
