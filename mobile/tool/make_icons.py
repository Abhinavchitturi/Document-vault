"""Draw the app icon (a document with a lock on the vault's indigo-violet gradient) in every
Android size. Run again after changing the design:  python tool/make_icons.py

    res/mipmap-*/ic_launcher.png              classic square icon (older Android)
    res/mipmap-*/ic_launcher_foreground.png   adaptive icon layer (Android 8+), the phone picks the shape
    res/drawable/ic_launcher_background.xml   its gradient background
    res/mipmap-anydpi-v26/ic_launcher.xml     ties the two together (+ monochrome for themed icons)
    res/drawable-xxhdpi/launch_logo.png       the logo on the white start-up screen
    ../frontend/app/icon.png                  the web app's browser-tab icon
"""

from pathlib import Path

from PIL import Image, ImageDraw

RES = Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "res"
TOP, BOTTOM = (79, 70, 229), (124, 58, 237)  # --accent, --accent-2 of the web app
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
S = 1024  # drawn large, then scaled down (smooth edges)


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):  # top-left to bottom-right feel: vertical is enough at icon size
        t = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return img


def glyph(size: int, scale: float, color=(255, 255, 255, 255), cutout=(0, 0, 0, 0)) -> Image.Image:
    """The document-and-lock drawing on a transparent square; `scale` is its share of the square."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size * scale / 100  # drawing units: the glyph is 100 x 100
    ox = oy = (size - 100 * u) / 2
    P = lambda x, y: (ox + x * u, oy + y * u)  # noqa: E731
    w = 7 * u  # stroke width
    # the page, its top-right corner folded over
    d.polygon([P(14, 6), P(52, 6), P(70, 24), P(70, 94), P(14, 94)], fill=color)
    d.polygon([P(52, 6), P(52, 24), P(70, 24)], fill=cutout)
    d.polygon([P(52, 8), P(52, 24), P(68, 24)], fill=color[:3] + (150,))  # the fold, lighter
    # text lines
    for y, x2 in ((36, 58), (48, 58), (60, 44)):
        d.rounded_rectangle([P(24, y - 2.6), P(x2, y + 2.6)], radius=2.6 * u, fill=cutout)
    # the lock, bottom right, standing clear of the page
    d.rounded_rectangle([P(46, 60), P(96, 99)], radius=8 * u, fill=cutout)  # gap around the body
    d.pieslice([P(53, 39), P(89, 75)], 180, 360, fill=cutout)  # gap around and inside the shackle
    d.rectangle([P(53, 56), P(89, 64)], fill=cutout)
    d.rounded_rectangle([P(50, 64), P(92, 95)], radius=6 * u, fill=color)  # body
    d.arc([P(58, 44), P(84, 70)], 180, 360, fill=color, width=round(w))  # shackle
    d.rectangle([P(58, 56), P(58 + w / u, 66)], fill=color)
    d.rectangle([P(84 - w / u, 56), P(84, 66)], fill=color)
    d.ellipse([P(67, 72), P(75, 80)], fill=cutout)  # keyhole
    d.rectangle([P(69.6, 78), P(72.4, 87)], fill=cutout)
    return img


def classic(px: int) -> Image.Image:
    big = gradient(S).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=255)
    big.putalpha(mask)
    big.alpha_composite(glyph(S, 0.62))
    return big.resize((px, px), Image.LANCZOS)


def main() -> None:
    for name, k in DENSITIES.items():
        folder = RES / f"mipmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        classic(round(48 * k)).save(folder / "ic_launcher.png")
        # adaptive foreground: 108dp canvas; the glyph stays inside the 66dp circle every shape keeps
        glyph(S, 0.44).resize((round(108 * k),) * 2, Image.LANCZOS).save(folder / "ic_launcher_foreground.png")
    (RES / "drawable").mkdir(exist_ok=True)
    (RES / "drawable" / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- The adaptive icon\'s background: the vault\'s indigo-violet gradient (tool/make_icons.py). -->\n'
        '<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">\n'
        '    <gradient android:angle="270" android:startColor="#4F46E5" android:endColor="#7C3AED" />\n'
        '</shape>\n')
    (RES / "mipmap-anydpi-v26").mkdir(exist_ok=True)
    (RES / "mipmap-anydpi-v26" / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@drawable/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n')
    (RES / "drawable-xxhdpi").mkdir(exist_ok=True)  # 288 px at xxhdpi = 96 dp on every phone
    classic(288).save(RES / "drawable-xxhdpi" / "launch_logo.png")
    # the same icon as the web app's browser-tab icon
    classic(192).save(Path(__file__).resolve().parents[2] / "frontend" / "app" / "icon.png")


if __name__ == "__main__":
    main()
