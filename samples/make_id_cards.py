"""Make fictional identity documents to test reading ID cards (Hindi + English, photos, locked PDFs).

    samples/scans/identity/PAN Card (photo).jpg         a phone photo of a PAN-style card
    samples/scans/identity/Aadhaar Card (scan).png      a scanned Aadhaar-style card, front
    samples/scans/identity/e-Aadhaar (locked).pdf       the same as an image-only PDF locked with a
                                                        password, like the real e-Aadhaar download
                                                        (password: first 4 letters of the name in
                                                        capitals + year of birth = ARJU1988)

Every card says SPECIMEN; the names and numbers are made up (the Aadhaar number fails the
real checksum on purpose). Rerun after editing: python samples/make_id_cards.py
(needs `pip install pikepdf` for the locked PDF, and a Devanagari font such as Noto Sans
Devanagari).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "scans" / "identity"
HINDI_FONTS = ("NotoSansDevanagari-Regular.ttf", "Kohinoor.ttc", "DevanagariMT.ttc")
LATIN_FONTS = ("NotoSans-Regular.ttf", "DejaVuSans.ttf", "Arial.ttf")
FONT_DIRS = ("/usr/share/fonts/truetype/noto", "/usr/share/fonts/truetype/dejavu", "/System/Library/Fonts",
             "/System/Library/Fonts/Supplemental", "/Library/Fonts", "")


def font(size: int, bold: bool = False, hindi: bool = False):
    names = HINDI_FONTS if hindi else LATIN_FONTS
    names = [n.replace("Regular", "Bold") for n in names] + list(names) if bold else list(names)
    for name in names:
        for folder in FONT_DIRS:
            try:
                return ImageFont.truetype(str(Path(folder) / name) if folder else name, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def runs(text: str) -> list[tuple[str, bool]]:
    """Split a line into Hindi (Devanagari) and Latin pieces: each needs its own font."""
    out: list[tuple[str, bool]] = []
    for ch in text:
        hindi = "\u0900" <= ch <= "\u097f" or (ch in " ,/" and out and out[-1][1] and ch != "/")
        if out and (out[-1][1] == hindi or ch == " "):
            out[-1] = (out[-1][0] + ch, out[-1][1])
        else:
            out.append((ch, hindi))
    return out


def write(draw, xy, text: str, pt: int, bold: bool, fill):
    x, y = xy
    for piece, hindi in runs(text):
        f = font(pt, bold, hindi)
        draw.text((x, y), piece, font=f, fill=fill)
        x += draw.textlength(piece, font=f)


def card(lines: list[tuple[str, int, bool]], size=(1011, 638), paper=(236, 244, 250)) -> Image.Image:
    """A credit-card-sized card at ~300 dpi: a coloured band, an emblem-like mark, a photo box, text."""
    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, size[0], 70], fill=(255, 153, 51))  # saffron band, like many Indian ID cards
    draw.ellipse([30, 90, 110, 170], outline=(90, 90, 90), width=4)  # the emblem's place
    draw.rectangle([size[0] - 250, 110, size[0] - 60, 350], outline=(120, 120, 120), width=3)  # photo
    draw.text((size[0] - 215, 210), "PHOTO", font=font(28), fill=(150, 150, 150))
    y = 12
    for text, pt, bold in lines:
        write(draw, (130 if y < 180 else 40, y), text, pt, bold, (25, 25, 25))
        y += round(pt * 1.55)
    draw.text((40, size[1] - 50), "SPECIMEN - NOT A REAL DOCUMENT", font=font(22, True), fill=(200, 40, 40))
    return image


PAN = [
    ("आयकर विभाग   INCOME TAX DEPARTMENT", 30, True),
    ("भारत सरकार   GOVT. OF INDIA", 26, False),
    ("स्थायी लेखा संख्या कार्ड", 24, False),
    ("Permanent Account Number Card", 24, False),
    ("ABCPM1234K", 40, True),
    ("नाम / Name", 20, False),
    ("ARJUN MEHRA", 30, True),
    ("पिता का नाम / Father's Name", 20, False),
    ("RAJESH MEHRA", 28, False),
    ("जन्म की तारीख / Date of Birth", 20, False),
    ("14/07/1988", 28, False),
]

AADHAAR = [
    ("भारत सरकार", 30, True),
    ("Government of India", 30, True),
    ("",  30, False),
    ("अर्जुन मेहरा", 30, False),
    ("Arjun Mehra", 30, False),
    ("जन्म तिथि / DOB: 14/07/1988", 26, False),
    ("पुरुष / MALE", 26, False),
    ("",  20, False),
    ("2345 6789 0124", 44, True),
    ("VID : 9134 5678 2345 6781", 20, False),  # the Virtual ID: 16 digits, not the Aadhaar number
    ("मेरा आधार, मेरी पहचान", 24, False),
]

AADHAAR_BACK = [  # the address side (page 2 of an e-Aadhaar)
    ("भारतीय विशिष्ट पहचान प्राधिकरण", 26, True),
    ("Unique Identification Authority of India", 26, True),
    ("", 20, False),
    ("पता:", 22, False),
    ("Address:", 24, True),
    ("S/O Rajesh Mehra, H.No 2-11/A,", 26, False),
    ("Gandhi Nagar, Hyderabad,", 26, False),
    ("Telangana - 500080", 26, False),
    ("", 20, False),
    ("2345 6789 0124", 36, True),
]


def pan_photo() -> Path:
    image = card(PAN).rotate(-3, expand=True, fillcolor=(70, 60, 55), resample=Image.BICUBIC)  # held a little crooked
    image = image.filter(ImageFilter.GaussianBlur(0.7))
    path = OUT / "PAN Card (photo).jpg"
    image.save(path, "JPEG", quality=82)
    return path


def aadhaar_scan() -> Image.Image:
    image = card(AADHAAR, paper=(250, 250, 246))
    image.save(OUT / "Aadhaar Card (scan).png")
    return image


def locked_pdf(image: Image.Image) -> None:
    import pikepdf

    plain = OUT / "_plain.pdf"
    back = card(AADHAAR_BACK, paper=(250, 250, 246))
    image.save(plain, "PDF", resolution=200, save_all=True, append_images=[back])
    with pikepdf.open(plain) as pdf:
        pdf.save(OUT / "e-Aadhaar (locked).pdf", encryption=pikepdf.Encryption(user="ARJU1988", owner="ARJU1988-owner", R=6))
    plain.unlink()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    pan_photo()
    locked_pdf(aadhaar_scan())
    print(f"Wrote identity samples to {OUT}")
