"""Make fictional scanned documents and phone photos to test OCR (Phase 4).

    samples/scans/bill/Gas Bill 2026 07 (scan).pdf
        a PDF with no text layer, scanned upside down
    samples/scans/warranty/Water Purifier Warranty Card (photo).jpg
        a phone photo: grey and low contrast, slightly tilted, stored sideways with an
        EXIF "rotate 90°" tag the way phones do

Upload them like any other file; the app straightens them, reads them with Tesseract and
highlights answers on the image. They are committed; rerun this only after editing the text:

    python samples/make_scans.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "scans"

GAS_BILL = """BHARAT CITY GAS LIMITED
Piped Natural Gas Bill
Customer: A. Sharma
Consumer number: PNG-4471-2086
Address: Flat 302, Lakeview Residency, Hyderabad
Billing period: 1 June 2026 to 31 July 2026
Previous reading: 1,284 SCM
Current reading: 1,327 SCM
Gas consumed: 43 SCM
Gas charges: Rs. 1,892
Taxes: Rs. 94
Total amount due: Rs. 1,986
Due date: 20 August 2026
Late payment fee after due date: Rs. 50
Emergency helpline: 1800 425 7788"""

PURIFIER_CARD = """AQUAPURE WARRANTY CARD
Product: AquaPure RO 700 water purifier
Model no: AP-RO700
Serial no: AP7-2291-0457
Date of purchase: 12 March 2025
Dealer: Sai Home Appliances, Kukatpally
Warranty: 2 years from date of purchase
Filters and membrane: 6 months
Service centre: 1800 102 3344"""


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _page(text: str, size: tuple[int, int], font_size: int, paper="white", ink="black") -> Image.Image:
    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    font, y = _font(font_size), font_size * 3
    for i, line in enumerate(text.splitlines()):
        draw.text((font_size * 3, y), line, font=_font(round(font_size * 1.3)) if i == 0 else font, fill=ink)
        y += round(font_size * 2.1)
    return image


def gas_bill_scan() -> None:
    page = _page(GAS_BILL, (1275, 1650), 26)  # A4-ish at 150 dpi
    page = page.rotate(180).filter(ImageFilter.GaussianBlur(0.6))  # fed into the scanner upside down
    path = OUT / "bill" / "Gas Bill 2026 07 (scan).pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.save(path, "PDF", resolution=150)


def purifier_photo() -> None:
    card = _page(PURIFIER_CARD, (1400, 900), 30, paper=(196, 190, 178), ink=(88, 84, 80))  # dim, grey
    card = card.rotate(2.5, expand=True, fillcolor=(120, 116, 110), resample=Image.BICUBIC)  # not held straight
    card = card.filter(ImageFilter.GaussianBlur(0.8))
    stored = card.rotate(90, expand=True)  # the sensor's raw orientation...
    exif = Image.Exif()
    exif[0x0112] = 6  # ...and the tag that tells viewers to turn it back
    path = OUT / "warranty" / "Water Purifier Warranty Card (photo).jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    stored.save(path, "JPEG", quality=80, exif=exif)


if __name__ == "__main__":
    gas_bill_scan()
    purifier_photo()
    print(f"Wrote scans to {OUT}")
