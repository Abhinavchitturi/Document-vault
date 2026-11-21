#!/bin/sh
# Build "Document Vault.app", the native Mac app, into apps/apple/macOS/build/. Needs Xcode (SwiftUI).
#   sh apps/apple/macOS/build_app.sh [version]
# The app starts the vault's engine from the vault folder (~/Document-Vault, or $DOCUMENT_VAULT_DIR when run from
# Terminal). It is signed ad hoc, not with an Apple Developer ID.
set -eu
cd "$(dirname "$0")"
VERSION="${1:-1.0.0}"
swift build -c release --product DocumentVault
APP="build/Document Vault.app"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/DocumentVault" "$APP/Contents/MacOS/Document Vault"

ICONSET="build/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size ../../../packaging/icons/icon-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) ../../../packaging/icons/icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

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
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST
codesign --force --sign - "$APP"
echo "Built apps/apple/macOS/$APP"
