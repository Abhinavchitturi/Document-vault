#!/bin/sh
# Build dist/DocumentVault-macOS.dmg: the native app (SwiftUI, apps/apple/macOS) with its engine inside it
# (packaging/macos/build_engine.sh: its own Python, the vault's code, Tesseract), and a link to Applications to drag
# it onto. Nothing else needs installing. Run on a Mac with Xcode:  sh packaging/macos/build_dmg.sh [version]
#
# The app is signed ad hoc, not with an Apple Developer ID, so the first time it is opened macOS asks the
# person to allow it (System Settings → Privacy & Security → Open Anyway).
set -eu
cd "$(dirname "$0")/../.."
VERSION="${1:-1.0.0}"
OUT="dist"
ENGINE="$OUT/engine-mac"
APP="$OUT/build/Document Vault.app"

[ -x "$ENGINE/python/bin/python3" ] || sh packaging/macos/build_engine.sh "$ENGINE"
sh apps/apple/macOS/build_app.sh "$VERSION"
rm -rf "$OUT/build" && mkdir -p "$OUT/build"
ditto "apps/apple/macOS/build/Document Vault.app" "$APP"
ditto "$ENGINE" "$APP/Contents/Resources/Engine"
codesign --force --sign - "$APP"  # seals the engine in with the app
codesign --verify "$APP"
STAGE="$OUT/build/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT/DocumentVault-macOS.dmg"
hdiutil create -quiet -volname "Document Vault" -srcfolder "$STAGE" -ov -format UDZO "$OUT/DocumentVault-macOS.dmg"
rm -rf "$OUT/build"
echo "Built $OUT/DocumentVault-macOS.dmg"
