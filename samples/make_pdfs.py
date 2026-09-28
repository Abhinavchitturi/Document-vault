"""Turn the fictional sample documents (samples/documents/**/*.md) into PDFs.

The PDFs are what you'd actually upload in the app ("Bike Insurance 2026.pdf"),
so they exercise the real PDF path: text layer extraction, page numbers, and
"open at page N" links. They are committed under samples/pdfs/; rerun this after
editing a sample:

    python samples/make_pdfs.py

No PDF library needed: it writes plain PDF 1.4 with the standard Helvetica fonts.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, OUT = HERE / "documents", HERE / "pdfs"

PAGE_W, PAGE_H, MARGIN = 612, 792, 64
LINES_PER_PAGE = 44
WRAP = 92


def _escape(text: str) -> str:
    text = text.encode("ascii", "replace").decode()
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def layout(markdown: str) -> list[list[tuple[str, int, str]]]:
    """Markdown -> pages of (font, size, text) lines. '#' lines become bold headings."""
    lines: list[tuple[str, int, str]] = []
    for raw in markdown.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            lines.append(("F2", {1: 16, 2: 13}.get(level, 11), stripped.lstrip("#").strip()))
        elif not stripped:
            lines.append(("F1", 10, ""))
        else:
            lines.extend(("F1", 10, part) for part in textwrap.wrap(stripped, WRAP) or [""])
    return [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]


def build_pdf(pages: list[list[tuple[str, int, str]]]) -> bytes:
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,  # page tree, filled in once page ids are known
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for page in pages:
        y, ops = PAGE_H - MARGIN, []
        for font, size, text in page:
            y -= size + 6
            if text:
                ops.append(f"BT /{font} {size} Tf {MARGIN} {y} Td ({_escape(text)}) Tj ET")
        stream = "\n".join(ops)
        objs.append(f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream")
        content_id = len(objs)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"

    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def pdf_name(md_path: Path) -> str:
    """bike_insurance_2026.md -> 'Bike Insurance 2026.pdf'."""
    return " ".join(w if w.isdigit() else w.capitalize() for w in md_path.stem.split("_")) + ".pdf"


def main() -> None:
    for md in sorted(SRC.rglob("*.md")):
        target = OUT / md.parent.name / pdf_name(md)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_pdf(layout(md.read_text(encoding="utf-8"))))
        print(f"{md.relative_to(HERE)} -> {target.relative_to(HERE)}")


if __name__ == "__main__":
    main()
