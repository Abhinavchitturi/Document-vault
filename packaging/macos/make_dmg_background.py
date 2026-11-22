"""Draw the DMG window's background: drag the app to Applications, and how to open it the first time (the app isn't
signed with an Apple Developer ID, so macOS asks once). Run on a Mac; the PNGs are committed:

    python packaging/macos/make_dmg_background.py   ->  packaging/macos/dmg-background.png (+ @2x)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
W, H = 660, 560  # the window's size, in points (room below for Finder's tab and status bars)
INK, SOFT, ACCENT, PAPER = (29, 29, 31), (110, 110, 115), (88, 86, 214), (245, 245, 247)


def font(size, weight="Regular"):
    try:
        f = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", size)
        f.set_variation_by_name(weight)
        return f
    except (OSError, ValueError):
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)


def draw(scale: int) -> Image.Image:
    s = lambda v: int(v * scale)  # noqa: E731
    image = Image.new("RGB", (s(W), s(H)), PAPER)
    d = ImageDraw.Draw(image)
    d.text((s(W / 2), s(34)), "Drag Document Vault to Applications", font=font(s(19), "Semibold"), fill=INK, anchor="mm")
    # the arrow between the app (left, at 165,150) and Applications (right, at 495,150): Finder draws both icons
    y = s(150)
    d.line([(s(250), y), (s(400), y)], fill=ACCENT, width=s(5))
    d.polygon([(s(412), y), (s(396), y - s(11)), (s(396), y + s(11))], fill=ACCENT)
    # the first time
    d.rounded_rectangle([s(30), s(262), s(W - 30), s(436)], radius=s(14), fill=(255, 255, 255), outline=(220, 220, 225), width=max(1, s(1)))
    d.text((s(52), s(282)), "Opening it the first time", font=font(s(15), "Semibold"), fill=INK)
    steps = [
        "Open Document Vault from Applications. If macOS says it can't check the app, click Done.",
        "Open System Settings → Privacy & Security, and scroll down to Security.",
        "Click Open Anyway next to “Document Vault”, then Open Anyway again.",
    ]
    for i, step in enumerate(steps):
        top = s(314 + i * 34)
        d.ellipse([s(52), top, s(74), top + s(22)], fill=ACCENT)
        d.text((s(63), top + s(11)), str(i + 1), font=font(s(13), "Semibold"), fill=(255, 255, 255), anchor="mm")
        d.text((s(86), top + s(11)), step, font=font(s(12.5)), fill=SOFT, anchor="lm")
    d.text((s(W / 2), s(420)), "Only once: after that it opens normally, and it updates itself.",
           font=font(s(11.5)), fill=SOFT, anchor="mm")
    return image


draw(1).save(HERE / "dmg-background.png")
draw(2).save(HERE / "dmg-background@2x.png")
print("wrote", HERE / "dmg-background.png")
