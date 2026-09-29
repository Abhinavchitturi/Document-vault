"""Phase 4: scans and phone photos are straightened, read, scored and highlighted."""

import io
from pathlib import Path

import pytest
from PIL import Image, ImageFilter, ImageOps

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.parser.extract import extract_pages
from backend.parser.ocr import LOW_CONFIDENCE, ocr_available, ocr_sidecar, read_image
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

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
    client = signed_in(create_app(store, FakeEmbedder(), RegexTokenizer()))
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


def test_a_heic_phone_photo_and_a_pdf_without_extension_upload_and_read(tmp_path):
    import pillow_heif

    pillow_heif.register_heif_opener()
    heic = io.BytesIO()
    with Image.open(PHOTO) as photo:
        ImageOps.exif_transpose(photo).save(heic, "HEIF")
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer(), frontend_dir=None))
    added = client.post("/api/documents", files=[("files", ("IMG_2031", heic.getvalue())),
                                                 ("files", ("Gas bill from WhatsApp", GAS_BILL.read_bytes()))]).json()
    assert [(a["status"], a["document"]["filename"]) for a in added] == [
        ("added", "IMG_2031.heic"), ("added", "Gas bill from WhatsApp.pdf")]
    assert all(a["document"]["ocr_pages"] == 1 for a in added)  # both were read


def test_a_photo_with_a_shadow_across_it_is_still_read(tmp_path):
    """A hand's or phone's shadow over part of the card: the lighting is evened out and read again."""
    import numpy as np

    with Image.open(PHOTO) as photo:
        pixels = np.asarray(ImageOps.exif_transpose(photo).convert("RGB")).astype(float)
    h, w = pixels.shape[:2]
    light = 0.35 + 0.65 * np.clip(0.72 * np.linspace(0, 1, w)[None, :, None] + 0.6 * np.linspace(0, 1, h)[:, None, None], 0, 1)
    shadowed = Image.fromarray(np.clip(pixels * light, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    page = read_image(shadowed)
    assert page.confidence >= LOW_CONFIDENCE + 10  # not "hard to read"
    assert "warranty" in page.text.lower()
