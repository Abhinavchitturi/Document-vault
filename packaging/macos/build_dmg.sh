#!/bin/sh
# Build dist/DocumentVault-macOS.dmg: the native app (SwiftUI, apps/apple/macOS) with the vault's code inside it, and a
# link to Applications to drag it onto. The engine (packaging/macos/build_engine.sh: Python, its packages, Tesseract)
# is a separate release file that the app downloads only when the Mac keeps the vault; the app carries engine.json
# (where, and its SHA-256). Run on a Mac with Xcode:
#   sh packaging/macos/build_dmg.sh [version tag, e.g. v0.9.1]
#
# The app is signed ad hoc, not with an Apple Developer ID, so the first time it is opened macOS asks the
# person to allow it (System Settings → Privacy & Security → Open Anyway).
set -eu
cd "$(dirname "$0")/../.."
TAG="${1:-v0.0.0-dev}"
OUT="dist"
ENGINE="$OUT/engine-mac"
APP="$OUT/build/Document Vault.app"

[ -f "$OUT/engine.json" ] || sh packaging/macos/build_engine.sh "$ENGINE" "$TAG"
if [ ! -x "$ENGINE/python/bin/python3" ]; then  # a reused engine: unpack it, to compile the code with its Python
  mkdir -p "$ENGINE" && tar -xJf "$OUT"/DocumentVault-Engine-macOS-*.tar.xz -C "$ENGINE"
fi
sh apps/apple/macOS/build_app.sh "${TAG#v}"
rm -rf "$OUT/build" && mkdir -p "$OUT/build"
ditto "apps/apple/macOS/build/Document Vault.app" "$APP"
cp "$OUT/engine.json" "$APP/Contents/Resources/engine.json"
cp -R backend "$APP/Contents/Resources/backend"  # the vault's code: updated with the app
find "$APP/Contents/Resources/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
"$ENGINE/python/bin/python3" -m compileall -q "$APP/Contents/Resources/backend"  # compiled now: the app never writes into itself
codesign --force --sign - "$APP"
codesign --verify "$APP"
STAGE="$OUT/build/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
rm -f "$OUT/DocumentVault-macOS.dmg"
# The window people see: the app, an arrow to Applications, and how to open it the first time (it isn't signed with
# an Apple Developer ID, so macOS asks once). packaging/macos/make_dmg_background.py draws the picture.
tiffutil -cathidpicheck packaging/macos/dmg-background.png packaging/macos/dmg-background@2x.png -out "$OUT/build/background.tiff"
command -v create-dmg >/dev/null 2>&1 || brew install create-dmg >/dev/null 2>&1 || true
if ! command -v create-dmg >/dev/null 2>&1 || ! create-dmg --volname "Document Vault" --background "$OUT/build/background.tiff" \
     --window-pos 200 100 --window-size 660 580 --icon-size 96 --text-size 13 \
     --icon "Document Vault.app" 165 150 --hide-extension "Document Vault.app" --app-drop-link 495 150 \
     --format ULMO --no-internet-enable "$OUT/DocumentVault-macOS.dmg" "$STAGE"; then
  echo "Laying out the window didn't work here; building a plain DMG instead" >&2
  rm -f "$OUT/DocumentVault-macOS.dmg"
  cp packaging/macos/dmg-background@2x.png "$STAGE/How to open the first time.png"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -quiet -volname "Document Vault" -srcfolder "$STAGE" -ov -format ULMO "$OUT/DocumentVault-macOS.dmg"  # LZMA: half the size of zlib
fi
rm -rf "$OUT/build"
echo "Built $OUT/DocumentVault-macOS.dmg"
