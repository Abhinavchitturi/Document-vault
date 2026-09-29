"""Why won't a file upload, or why is it "hard to read"? Prints what the vault sees in it, without
printing any of its text (safe to paste into a chat):

    python -m backend.parser.diagnose ~/Downloads/some-file [--password ABCD1234]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from backend.parser.extract import PasswordRequiredError, UnsupportedFileError, extract_pages, file_type, sniff
from backend.parser.ocr import LOW_CONFIDENCE, ocr_available, ocr_languages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--password", default=None, help="for a locked PDF")
    args = parser.parse_args()
    path = args.file.expanduser()
    if not path.is_file():
        raise SystemExit(f"No such file: {path}")
    with open(path, "rb") as f:
        head = f.read(12)
    print(f"File: {path.name} ({path.stat().st_size // 1024} KB), starts with {head[:12]!r}")
    try:
        print(f"Looks like: {sniff(path) or 'unknown'}; will be read as: {file_type(path, path.name)}")
    except UnsupportedFileError as e:
        raise SystemExit(f"Can't be read: {e}") from e
    print(f"Scan reading: {'Tesseract, languages ' + ocr_languages() if ocr_available() else 'NOT INSTALLED (brew install tesseract tesseract-lang)'}")
    started = time.time()
    try:
        pages = extract_pages(path, password=args.password)
    except PasswordRequiredError as e:
        raise SystemExit("Locked PDF: run again with --password (wrong password)." if e.wrong
                         else "Locked PDF: run again with --password.") from e
    print(f"Pages: {len(pages)}, read in {time.time() - started:.1f} s")
    for p in pages:
        conf = p.ocr_data.confidence if p.ocr_data else None
        how = f"scan-read, confidence {conf}%" + (" -> HARD TO READ" if conf is not None and conf < LOW_CONFIDENCE else "") \
            if p.ocr else "text layer (no scan-reading needed)"
        print(f"  page {p.number}: {len(p.text)} characters, {len(p.text.split())} words, {how}")
    if not any(p.text.strip() for p in pages):
        print("No text found at all: if it's a photo, retake it flat, in good light and in focus.")


if __name__ == "__main__":
    main()
