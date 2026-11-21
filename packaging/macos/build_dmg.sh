#!/bin/sh
# Build dist/DocumentVault-macOS.dmg: "Document Vault.app" (packaging/macos/launcher.sh) and a link to
# Applications, to drag it onto. Run on a Mac:  sh packaging/macos/build_dmg.sh [version]
#
# The app is signed ad hoc, not with an Apple Developer ID, so the first time it is opened macOS asks the
# person to allow it (System Settings → Privacy & Security → Open Anyway).
set -eu
cd "$(dirname "$0")/../.."
VERSION="${1:-1.0.0}"
OUT="dist"
APP="$OUT/build/Document Vault.app"

rm -rf "$OUT/build" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp packaging/macos/launcher.sh "$APP/Contents/MacOS/Document Vault"
chmod +x "$APP/Contents/MacOS/Document Vault"

# the icon, in every size macOS asks for
ICONSET="$OUT/build/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size packaging/icons/icon-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) packaging/icons/icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Document Vault</string>
  <key>CFBundleDisplayName</key><string>Document Vault</string>
  <key>CFBundleIdentifier</key><string>app.documentvault.mac</string>
  <key>CFBundleExecutable</key><string>Document Vault</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
codesign --force --deep --sign - "$APP"

STAGE="$OUT/build/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT/DocumentVault-macOS.dmg"
hdiutil create -quiet -volname "Document Vault" -srcfolder "$STAGE" -ov -format UDZO "$OUT/DocumentVault-macOS.dmg"
rm -rf "$OUT/build"
echo "Built $OUT/DocumentVault-macOS.dmg"
