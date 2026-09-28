"""OCR for photos and scanned pages: straighten, clean up, read, and remember where each word is.

    1. Orientation  phone photos carry an EXIF rotation; scans are often sideways or upside down.
                    Both are fixed before reading (Tesseract's orientation detection, "OSD").
    2. Clean-up     grayscale, stretch the contrast (faded thermal receipts, grey photos), and
                    enlarge small images so letters are big enough to read.
    3. Read         Tesseract returns every word with its box and a confidence (0-100).

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


def _cleaned(image):
    """Grayscale, contrast stretched, enlarged if small. Returns (image, scale factor)."""
    from PIL import Image, ImageOps

    gray = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    scale = 1.0
    if gray.width < MIN_OCR_WIDTH:
        scale = MIN_OCR_WIDTH / gray.width
        gray = gray.resize((MIN_OCR_WIDTH, round(gray.height * scale)), Image.LANCZOS)
    return gray, scale


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
    cleaned, scale = _cleaned(image)
    data = pytesseract.image_to_data(cleaned, output_type=pytesseract.Output.DICT)

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
