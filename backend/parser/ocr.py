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
import re
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
    sure_chars: int = field(default=0, repr=False, compare=False)  # letters/digits read >= 80% sure (not saved)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != "sure_chars"}

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


def _cleaned(image, width: int = MIN_OCR_WIDTH, flatten: bool = False):
    """Grayscale, contrast stretched, enlarged if narrower than `width`. Returns (image, scale factor).
    `flatten` evens out the lighting first (a phone's shadow across the page, glare at one corner)."""
    from PIL import Image, ImageOps

    gray = image.convert("L")
    if flatten:
        gray = _flatten_light(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    scale = 1.0
    if gray.width < width:
        scale = width / gray.width
        gray = gray.resize((width, round(gray.height * scale)), Image.LANCZOS)
    return gray, scale


def _flatten_light(gray):
    """Divide the page by a heavily blurred copy of itself: the blur is the lighting (shadow, glare,
    a dim corner), so what's left is the ink on an evenly lit, white page."""
    import numpy as np
    from PIL import Image, ImageFilter

    # The paper's brightness: at a quarter of the size, a maximum filter wipes out the ink (strokes are
    # thinner than its window, even big bold numbers), then a blur smooths it into the lighting.
    small = gray.resize((max(1, gray.width // 4), max(1, gray.height // 4)))
    paper = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(4)).resize(gray.size)
    background = np.asarray(paper, dtype=np.float32)
    ink = np.asarray(gray, dtype=np.float32)
    even = np.clip(ink / np.maximum(background, 1.0) * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(even)


def _is_word(token: str) -> bool:
    return sum(ch.isalnum() for ch in token) >= 2


def read_image(image, rotation: int | None = None) -> OcrPage:
    """OCR one page image. `rotation` skips orientation detection when it's already known."""
    require_ocr()

    if rotation is None:
        image, rotation = upright(image)
    else:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image).convert("RGB")
    image = rotate(image, rotation)
    # Read as it is and with the lighting evened out (a phone's shadow or glare can hide most of a
    # page while the few words left read "confidently"); the fuller, surer reading wins.
    page = max((_read(image, rotation, MIN_OCR_WIDTH), _read(image, rotation, MIN_OCR_WIDTH, flatten=True)), key=_score)
    tries = []
    if image.width < RETRY_WIDTH:
        tries.append(dict(width=RETRY_WIDTH))  # small print (ID cards, receipts) reads better larger
    if "+" in ocr_languages() and _devanagari_share(page.text) < 0.05:
        tries.append(dict(width=MIN_OCR_WIDTH, lang="eng"))  # English-only pages read better as English
    for options in tries:
        if (page.confidence or 0) >= GOOD_ENOUGH:
            break
        for flatten in (False, True):
            other = _read(image, rotation, flatten=flatten, **options)
            if _score(other) > _score(page):
                page = other
    return page


GOOD_ENOUGH = 85  # a page read at least this confidently isn't read again


def _score(page: OcrPage) -> float:
    """How much of the page was read surely: letters and digits in words read at least 80% sure.
    Average confidence alone would prefer a reading that is very sure of three words and missed the
    rest of the page; a count of all characters would prefer one padded with doubtful garbage."""
    return page.sure_chars


def _devanagari_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    return sum("\u0900" <= ch <= "\u097f" for ch in letters) / len(letters) if letters else 0.0


def _read(image, rotation: int, width: int, flatten: bool = False, lang: str | None = None) -> OcrPage:
    import pytesseract

    cleaned, scale = _cleaned(image, width, flatten)
    data = pytesseract.image_to_data(cleaned, lang=lang or ocr_languages(), output_type=pytesseract.Output.DICT)

    entries = []  # (word, confidence, box in the cleaned image, line key, block key)
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        block = (data["block_num"][i], data["par_num"][i])
        box = [data[k][i] for k in ("left", "top", "width", "height")]
        entries.append((ftfy.fix_text(word), conf, box, (*block, data["line_num"][i]), block))
    entries = [(*_reread_mixed(cleaned, w, c, box), box, line, block) for w, c, box, line, block in entries]

    text, words, confs, sure = "", [], [], 0
    last_line = last_block = None
    for word, conf, box, line, block in entries:
        if last_line is not None:
            text += "\n\n" if block != last_block else "\n" if line != last_line else " "
        last_line, last_block = line, block
        start = len(text)
        text += word
        words.append([*(round(v / scale) for v in box), start, len(text)])
        if _is_word(word):  # specks from logos and photos don't count against the page
            confs.append((conf, len(word)))
            if conf >= 80:
                sure += sum(ch.isalnum() for ch in word)
    total = sum(n for _, n in confs)
    confidence = round(sum(c * n for c, n in confs) / total) if total else None
    return OcrPage(text, image.width, image.height, rotation, confidence, words, sure)


# House / door numbers and similar: "2-11", "2-11/A", "12/B". Mixed Hindi-English reading sometimes
# turns a digit into a look-alike letter ("R-11", "Z-11") in exactly these.
_MIXED = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9]{1,4}(?:[-/][A-Za-z0-9]{1,4}){1,3}$")
SURE = 85  # words read at least this confidently are left alone ("B-11" really is B-11)
BETTER_BY = 10  # a second reading must be this much more confident to replace the first


def _reread_mixed(image, word: str, conf: float, box: list[int]) -> tuple[str, float]:
    """A doubtful letters-and-digits word is read again on its own, in English only and as digits
    only; the most confident reading wins, if it is clearly more confident than the first."""
    if conf >= SURE or not _MIXED.match(word):
        return word, conf
    import pytesseract

    x, y, w, h = box
    pad = max(4, h // 3)
    crop = image.crop((max(0, x - pad), max(0, y - pad), x + w + pad, y + h + pad))
    best = (word, conf)
    for config in ("--psm 7", "--psm 7 -c tessedit_char_whitelist=0123456789-/"):
        try:
            data = pytesseract.image_to_data(crop, lang="eng", config=config, output_type=pytesseract.Output.DICT)
        except Exception:  # noqa: BLE001 (a second opinion is optional)
            continue
        found = [(t.strip(), float(c)) for t, c in zip(data["text"], data["conf"]) if (t or "").strip() and float(c) >= 0]
        if len(found) != 1:
            continue  # it split or merged words: not a like-for-like reading
        text, c = found[0]
        # Only swap look-alikes: same length, same separators, digits where the first reading had a letter.
        same_shape = len(text) == len(word) and all(a == b for a, b in zip(text, word) if not a.isalnum() or not b.isalnum())
        if same_shape and c > best[1]:
            best = (text, c)
    if best[0] != word and best[1] >= conf + BETTER_BY:
        logger.debug("OCR: re-read %r as %r (%d -> %d)", word, best[0], conf, best[1])
        return best
    return word, conf


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
