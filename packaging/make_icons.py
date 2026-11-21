"""Desktop icons from the phone app's drawing (mobile/tool/make_icons.py), so every version looks the same.

    python packaging/make_icons.py

    packaging/icons/icon-1024.png        macOS: build_dmg.sh turns it into the app's .icns
    packaging/windows/DocumentVault.ico  Windows: the installer's and the shortcuts' icon
"""

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mobile" / "tool"))
from make_icons import classic  # noqa: E402


def main() -> None:
    # macOS icons leave a margin around the artwork (824 of 1024 pixels), like Apple's own
    mac = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mac.alpha_composite(classic(824), (100, 100))
    (ROOT / "packaging" / "icons").mkdir(parents=True, exist_ok=True)
    mac.save(ROOT / "packaging" / "icons" / "icon-1024.png")
    classic(256).save(ROOT / "packaging" / "windows" / "DocumentVault.ico",
                      sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
