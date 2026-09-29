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
    # the Aadhaar number, not the VID printed under it (16 digits, "VID : 9134 5678 2345 6781")
    assert doc["category"] == "identity" and doc["reference"] == "2345 6789 0124" and doc["ocr_pages"] == 2
    address = next(c["text"] for c in store.chunks if "Address" in c["text"])
    assert "H.No 2-11/A" in address  # the house number with its digits, from the address page
    with store.plain_copy(doc["doc_id"]) as stored:
        assert len(open_pdf(stored)) == 2  # the copy in the vault opens without the password (card + address)
    assert client.get(f"/api/documents/{doc['doc_id']}/pages/1/image").status_code == 200
    assert client.get("/api/search", params={"q": "2345 6789 0124"}).json()["results"][0]["doc_id"] == doc["doc_id"]


def test_a_vid_is_never_taken_for_the_aadhaar_number():
    from backend.extract.facts import Facts, RulesReader, grounded

    vid_first = "Government of India\nVID : 9134 5678 2345 6781\n2345 6789 0124"
    assert RulesReader().read(vid_first).reference == "2345 6789 0124"
    assert RulesReader().read("VID : 9134 5678 2345 6781").reference is None  # only a VID: no Aadhaar number
    assert grounded(Facts(reference="9134 5678 2345 6781"), vid_first).reference == "2345 6789 0124"  # a model's slip
    from backend.llm.answer import SYSTEM_PROMPT
    assert "VID" in SYSTEM_PROMPT and "NOT the Aadhaar number" in SYSTEM_PROMPT


def test_a_doubtful_house_number_is_read_again_as_digits(monkeypatch):
    """Mixed Hindi-English reading can turn the 2 of 'H.No 2-11/A' into R. A doubtful word that mixes
    letters and digits is read again on its own; the clearly more confident reading wins."""
    import pytesseract
    from PIL import Image

    calls = []

    def second_opinion(crop, lang, config, output_type):
        calls.append(config)
        digits = "whitelist" in config
        return {"text": ["2-11/A" if not digits else "2-11/4"], "conf": [93 if not digits else 70]}

    monkeypatch.setattr(pytesseract, "image_to_data", second_opinion)
    image = Image.new("L", (400, 100), 255)
    assert ocr._reread_mixed(image, "R-11/A", 58, [10, 10, 80, 30]) == ("2-11/A", 93)
    assert len(calls) == 2
    calls.clear()
    assert ocr._reread_mixed(image, "B-11", 96, [10, 10, 80, 30]) == ("B-11", 96)  # read confidently: left alone
    assert ocr._reread_mixed(image, "Hyderabad", 40, [10, 10, 80, 30]) == ("Hyderabad", 40)  # no digits: not a candidate
    assert calls == []
    monkeypatch.setattr(pytesseract, "image_to_data",
                        lambda crop, lang, config, output_type: {"text": ["2-11/A"], "conf": [66]})
    assert ocr._reread_mixed(image, "R-11/A", 60, [10, 10, 80, 30]) == ("R-11/A", 60)  # not clearly better: kept
