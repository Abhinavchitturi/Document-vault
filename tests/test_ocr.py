"""Phase 4: scans and phone photos are straightened, read, scored and highlighted."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageFilter, ImageOps

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.parser.extract import extract_pages
from backend.parser.ocr import LOW_CONFIDENCE, ocr_available, ocr_sidecar, read_image
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.skipif(not ocr_available(), reason="Tesseract isn't installed")

SCANS = Path(__file__).resolve().parent.parent / "samples" / "scans"
GAS_BILL = SCANS / "bill" / "Gas Bill 2026 07 (scan).pdf"
PHOTO = SCANS / "warranty" / "Water Purifier Warranty Card (photo).jpg"


def test_upside_down_scan_is_turned_and_read():
    [page] = extract_pages(GAS_BILL)
    assert page.ocr and page.ocr_data.rotation == 180
    assert "Total amount due: Rs. 1,986" in page.text and "20 August 2026" in page.text
    assert page.ocr_data.confidence >= LOW_CONFIDENCE


def test_phone_photo_follows_its_exif_rotation():
    [page] = extract_pages(PHOTO)
    assert "Serial no: AP7-2291-0457" in page.text
    assert page.ocr_data.width > page.ocr_data.height  # a landscape card, although stored portrait


def test_sideways_page_without_exif_is_detected():
    upright = ImageOps.exif_transpose(Image.open(PHOTO))
    result = read_image(upright.rotate(90, expand=True))
    assert result.rotation == 90 and "AP7-2291-0457" in result.text


def test_blurry_photo_gets_a_low_confidence():
    photo = ImageOps.exif_transpose(Image.open(PHOTO))
    blurry = photo.resize((photo.width // 3, photo.height // 3)).filter(ImageFilter.GaussianBlur(1.5))
    assert read_image(blurry).confidence < LOW_CONFIDENCE


def test_answers_are_highlighted_on_scans(tmp_path):
    store = DocumentStore(tmp_path / "index")
    client = TestClient(create_app(store, FakeEmbedder(), RegexTokenizer()))
    [bill, photo] = client.post("/api/documents", files=[
        ("files", (GAS_BILL.name, GAS_BILL.read_bytes())), ("files", (PHOTO.name, PHOTO.read_bytes())),
    ]).json()
    doc = bill["document"]
    assert doc["ocr_pages"] == 1 and doc["ocr_confidence"] >= LOW_CONFIDENCE
    assert photo["document"]["category"] == "warranty"
    stored = store.file_path(doc["doc_id"])
    assert ocr_sidecar(stored).exists()

    chunk = next(c for c in store.chunks if c["doc_id"] == doc["doc_id"])
    url = f"/api/documents/{doc['doc_id']}/pages/1"
    h = client.post(f"{url}/highlights", json={"chunk_id": chunk["chunk_id"],
                                                "answer": "Your gas bill is Rs. 1,986, due on 20 August 2026 [1]."}).json()
    assert h["kind"] == "scan" and h["passage_found"] and h["confidence"] >= LOW_CONFIDENCE
    assert h["facts_found"] == ["Rs. 1,986", "20 August 2026"]
    for x, y, w, hh in h["facts"]:
        assert 0 <= x and x + w <= h["width"] and 0 <= y and y + hh <= h["height"]

    # The page image is served straightened, with the same shape the boxes were measured on.
    image = client.get(f"{url}/image")
    assert image.headers["content-type"] == "image/jpeg"
    shown = Image.open(io.BytesIO(image.content))
    assert abs(shown.width / shown.height - h["width"] / h["height"]) < 0.01

    # Uploads from before Phase 4 have no saved OCR: it's done on first view and kept.
    ocr_sidecar(stored).unlink()
    again = client.post(f"{url}/highlights", json={"chunk_id": chunk["chunk_id"], "answer": "Rs. 1,986"}).json()
    assert again["facts_found"] == ["Rs. 1,986"] and ocr_sidecar(stored).exists()

    client.delete(f"/api/documents/{doc['doc_id']}")
    assert not stored.exists() and not ocr_sidecar(stored).exists()
