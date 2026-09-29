"""Identity documents: Hindi + English ID cards, photos, and password-locked PDFs (e-Aadhaar)."""

from pathlib import Path

import pytest

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.classify.classifier import Classifier
from backend.extract.facts import RulesReader, grounded
from backend.parser import ocr
from backend.parser.extract import PasswordRequiredError, _image_share, extract_pages, open_pdf
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

IDS = Path(__file__).resolve().parent.parent / "samples" / "scans" / "identity"
PAN = IDS / "PAN Card (photo).jpg"
LOCKED = IDS / "e-Aadhaar (locked).pdf"
needs_hindi = pytest.mark.skipif(not ocr.ocr_available() or "hin" not in ocr.ocr_languages(),
                                 reason="Tesseract with Hindi isn't installed")


def test_specks_from_logos_and_photos_do_not_count_against_a_page():
    assert ocr._is_word("PAN") and ocr._is_word("14/07/1988") and ocr._is_word("नाम")
    assert not ocr._is_word("©") and not ocr._is_word("|") and not ocr._is_word("a.")


@needs_hindi
def test_a_pan_card_photo_is_read_in_hindi_and_english():
    [page] = extract_pages(PAN)
    assert page.ocr_data.confidence >= 85  # not "hard to read"
    assert "ABCPM1234K" in page.text and "ARJUN MEHRA" in page.text and "आयकर विभाग" in page.text
    decision = Classifier(FakeEmbedder()).classify("IMG_2231.jpg", page.text)
    assert decision.category == "identity" and not decision.needs_review
    facts = grounded(RulesReader().read(page.text), page.text)
    assert (facts.reference, facts.issuer) == ("ABCPM1234K", "INCOME TAX DEPARTMENT")


def test_locked_pdfs_ask_for_their_password():
    with pytest.raises(PasswordRequiredError) as missing:
        open_pdf(LOCKED)
    assert not missing.value.wrong and "first 4 letters" in str(missing.value)
    with pytest.raises(PasswordRequiredError) as wrong:
        open_pdf(LOCKED, "ARJU1999")
    assert wrong.value.wrong
    pdf = open_pdf(LOCKED, "ARJU1988")
    assert _image_share(pdf[0]) > 0.9  # the whole page is a picture of the card
    pdf.close()


@needs_hindi
def test_an_e_aadhaar_is_unlocked_once_and_kept_unlocked_in_the_vault(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    client = signed_in(create_app(store, FakeEmbedder(), RegexTokenizer(), frontend_dir=None))
    upload = lambda **form: client.post("/api/documents", files=[("files", (LOCKED.name, LOCKED.read_bytes()))],  # noqa: E731
                                        data=form).json()[0]
    first = upload()
    assert (first["status"], first["code"]) == ("error", "password_required")
    assert upload(password="ARJU1999")["code"] == "wrong_password"
    added = upload(password="ARJU1988")
    assert added["status"] == "added"
    doc = added["document"]
    assert doc["category"] == "identity" and doc["reference"] == "2345 6789 0124" and doc["ocr_pages"] == 1
    stored = store.file_path(doc["doc_id"])
    assert len(open_pdf(stored)) == 1  # the copy in the vault opens without the password
    assert client.get(f"/api/documents/{doc['doc_id']}/pages/1/image").status_code == 200
    assert client.get("/api/search", params={"q": "2345 6789 0124"}).json()["results"][0]["doc_id"] == doc["doc_id"]
