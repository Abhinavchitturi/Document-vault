#!/bin/sh
# Build the vault's engine: a private Python with the vault's packages, the engine's code, and Tesseract (the scan
# reader) with English, page-orientation and Hindi, so a person installs nothing else (no Homebrew, Git, Node, Python
# or Terminal). Apple silicon Macs. It is packed as DocumentVault-Engine-macOS.tar.xz, a release file the app
# downloads only when the Mac keeps the vault; engine.json (where to download it, its SHA-256) goes into the app.
#
#   sh packaging/macos/build_engine.sh [dist/engine-mac] [v0.9.1]
#
# The Python and the Hindi data are pinned and checked against their SHA-256. Tesseract is Homebrew's build, copied
# with its libraries (dylibbundler) so it runs on a Mac without Homebrew. The AI model isn't included: the app
# downloads it with one click (backend/llm/local_ai.py), and the search model downloads on the first start.
set -eu
cd "$(dirname "$0")/../.."
OUT="${1:-dist/engine-mac}"
TAG="${2:-v0.0.0-dev}"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.11.18%2B20261026-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_SHA="cba3455e18516fbe8668cff2820c9b6a53447c552d612443c2b2fb60d390a908"
HINDI_URL="https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/hin.traineddata"
HINDI_SHA="4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825"

fetch() {  # url file sha
  curl -fsSL --retry 3 -o "$2" "$1"
  got=$(shasum -a 256 "$2" | cut -d' ' -f1)
  [ "$got" = "$3" ] || { echo "$2 doesn't match its pinned checksum ($got)" >&2; exit 1; }
}

WORK="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/engine-build"
rm -rf "$OUT" "$WORK" && mkdir -p "$OUT" "$WORK"

echo "1. A private Python, with the vault's packages"
fetch "$PYTHON_URL" "$WORK/python.tar.gz" "$PYTHON_SHA"
tar -xzf "$WORK/python.tar.gz" -C "$OUT"  # -> $OUT/python/bin/python3
PY="$OUT/python/bin/python3"
"$PY" -m pip install --quiet --no-warn-script-location --no-cache-dir -r requirements.txt
"$PY" -m pip uninstall --quiet -y pytest  # for the project's tests, not the app
# torch and faiss each bring their own copy of OpenMP (libomp), and two copies in one process crash it as soon as
# both are used (adding documents). faiss uses torch's copy instead.
SITE=$("$PY" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
if [ -f "$SITE/faiss/.dylibs/libomp.dylib" ] && [ -f "$SITE/torch/lib/libomp.dylib" ]; then
  rm "$SITE/faiss/.dylibs/libomp.dylib"
  ln -s ../../torch/lib/libomp.dylib "$SITE/faiss/.dylibs/libomp.dylib"
fi

echo "2. The engine's code, compiled (the app doesn't write into itself)"
cp -R backend "$OUT/backend"
find "$OUT/backend" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q "$OUT/backend"

echo "3. Tesseract, the scan reader, with its libraries"
brew list tesseract >/dev/null 2>&1 || brew install tesseract
brew list dylibbundler >/dev/null 2>&1 || brew install dylibbundler
TESS="$(brew --prefix tesseract)"
mkdir -p "$OUT/tesseract/tessdata"
cp "$TESS/bin/tesseract" "$OUT/tesseract/tesseract"
chmod u+w "$OUT/tesseract/tesseract"
dylibbundler -of -cd -b -x "$OUT/tesseract/tesseract" -d "$OUT/tesseract/lib" -p @executable_path/lib/ >/dev/null
cp "$TESS/share/tessdata/eng.traineddata" "$TESS/share/tessdata/osd.traineddata" "$OUT/tesseract/tessdata/"
fetch "$HINDI_URL" "$OUT/tesseract/tessdata/hin.traineddata" "$HINDI_SHA"
for f in "$OUT/tesseract/tesseract" "$OUT"/tesseract/lib/*.dylib; do codesign --force --sign - "$f"; done
if otool -L "$OUT/tesseract/tesseract" "$OUT"/tesseract/lib/*.dylib | grep -E "/opt/homebrew|/usr/local/(opt|Cellar)"; then
  echo "Tesseract still points into Homebrew" >&2; exit 1
fi

echo "4. Check that it all runs"
PYTHONPATH="$OUT" "$PY" -c "import backend.api.app, backend.engine.bridge, torch, faiss; print('engine imports ok')"
"$PY" -c "
import torch, faiss
x = torch.randn(1000, 768).numpy()
index = faiss.IndexFlatIP(768); index.add(x); index.search(x, 2)
print('torch and faiss work together')"
TESSDATA_PREFIX="$OUT/tesseract/tessdata" "$OUT/tesseract/tesseract" --list-langs
PYTHONPATH="$OUT" "$PY" -c "from backend.parser import ocr; print('bundled tesseract:', ocr.tesseract_path())"
echo "5. Leave out what only building software needs (C++ headers, static libraries, test suites, Tk)"
rm -rf "$SITE/torch/include" "$SITE/torch/share" "$SITE/torch/test" "$OUT/python/lib/tcl"* "$OUT/python/lib/tk"* \
       "$OUT/python/lib/python3.11/tkinter" "$OUT/python/lib/python3.11/idlelib" "$OUT/python/lib/python3.11/test" \
       "$OUT/python/lib/python3.11/turtledemo"
find "$OUT" -name "*.a" -type f -delete
PYTHONPATH="$OUT" "$PY" -c "import backend.api.app, backend.engine.bridge, torch, faiss; print('still imports ok')"
echo "Engine ready in $OUT: $(du -sh "$OUT" | cut -f1)"

echo "6. Pack it for the release, and describe it for the app"
ARCHIVE="$(dirname "$OUT")/DocumentVault-Engine-macOS.tar.xz"
rm -f "$ARCHIVE"
tar --options xz:compression-level=6,xz:threads=0 -cJf "$ARCHIVE" -C "$OUT" .
SHA=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
BYTES=$(stat -f %z "$ARCHIVE")
cat > "$(dirname "$OUT")/engine.json" <<JSON
{"version": "${TAG#v}", "sha256": "$SHA", "size": $BYTES,
 "url": "https://github.com/Abhinavchitturi/Document-vault/releases/download/$TAG/DocumentVault-Engine-macOS.tar.xz"}
JSON
echo "$ARCHIVE: $((BYTES / 1000000)) MB, sha256 $SHA"
