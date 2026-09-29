"""Phase 9: reading expiry dates, due dates, amounts, references and issuers."""

import sys
import types
from datetime import date, timedelta

import pytest

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.eval.facts_eval import load_cases, score
from backend.extract import facts as facts_module
from backend.extract.facts import Facts, FactsReader, RulesReader, dates_in, grounded, snippets
from backend.extract.run import refresh
from backend.vector_store.store import DocumentStore
from tests.fakes import PASSWORD, FakeEmbedder, signed_in

BILL = """Western Power Distribution Co. (fictional)
Consumer Number: 170024559812
Total amount payable: Rs. 2,486
Due Date: 18 September 2026"""


def test_dates_are_read_in_the_usual_formats():
    found = [d for _, d in dates_in("18 September 2026, 5 Sep 2026, 2026-07-15 and 04/10/2033")]
    assert found == [date(2026, 9, 18), date(2026, 9, 5), date(2026, 7, 15), date(2033, 10, 4)]


@pytest.mark.parametrize("pdfs", [False, True])
def test_patterns_read_every_labelled_fact_of_the_samples(pdfs):
    result = score(RulesReader(), load_cases(pdfs))
    assert result["right"] == result["total"] == 53, result["misses"]


def test_values_not_in_the_document_are_dropped():
    made_up = Facts(expires_on="2027-01-01", due_on="2026-09-18", amount="9999", reference="ABC-123",
                    issuer="Some Other Co", method="needle")
    kept = grounded(made_up, BILL)
    assert (kept.expires_on, kept.due_on, kept.amount, kept.reference, kept.issuer) == (None, "2026-09-18", None, None, None)
    assert grounded(Facts(amount="2,486.00"), BILL).amount == "2486"


class _Reader:
    def __init__(self, result=None, error=None, name="needle"):
        self.result, self.error, self.name = result, error, name

    def read(self, text):
        if self.error:
            raise self.error
        return self.result


def test_the_next_reader_takes_over_and_gaps_are_filled_by_patterns():
    reader = FactsReader(prefer="rules")
    reader.readers = [_Reader(error=RuntimeError("engine missing")), _Reader(Facts(due_on="2026-09-18", method="ollama"), name="ollama")]
    got = reader.read(BILL)
    assert got.method == "ollama" and got.due_on == "2026-09-18"
    assert got.amount == "2486" and got.reference == "170024559812"  # topped up from the patterns


def test_needle_gets_a_short_window_with_telemetry_off(monkeypatch):
    seen = {}

    def extract(text, schema, strict):
        seen.update(text=text, schema=schema)
        return {"due_on": "2026-09-18", "amount": 2486, "issuer": "Western Power Distribution Co."}

    fake = types.SimpleNamespace(extract=extract, ExtractionValidationError=ValueError)
    monkeypatch.setitem(sys.modules, "needle", fake)
    monkeypatch.delenv("NEEDLE_TELEMETRY", raising=False)
    reader = FactsReader(prefer="needle")
    assert reader.name == "needle"
    import os

    assert os.environ["NEEDLE_TELEMETRY"] == "0" and os.environ["DO_NOT_TRACK"] == "1"
    got = reader.read(BILL + "\n" + "Unrelated line\n" * 500)
    assert got.method == "needle" and got.amount == "2486" and got.issuer.startswith("Western Power")
    assert len(seen["text"]) <= 3000 and seen["schema"]["name"] == "record_document_facts"
    assert len(snippets("x\n" * 5000)) <= 3000


def test_a_needle_that_is_not_set_up_is_tried_once(monkeypatch):
    calls = []

    def extract(text, schema, strict):
        calls.append(1)
        raise ConnectionError("can't download needle3.cact")

    monkeypatch.setitem(sys.modules, "needle", types.SimpleNamespace(extract=extract, ExtractionValidationError=ValueError))
    reader = FactsReader(prefer="auto")
    assert reader.read(BILL).method == "rules" and reader.read(BILL).method == "rules"
    assert len(calls) == 1 and reader.name == "rules"


def test_uploads_get_facts_people_can_correct_them_and_upcoming_lists_them(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    app = create_app(store, FakeEmbedder(), RegexTokenizer(), frontend_dir=None)
    me = signed_in(app)
    soon = (date.today() + timedelta(days=20)).strftime("%d %B %Y")
    later = (date.today() + timedelta(days=200)).strftime("%d %B %Y")
    bill = me.post("/api/documents", files=[("files", ("bill.txt", BILL.replace("18 September 2026", soon).encode()))]).json()[0]["document"]
    policy = me.post("/api/documents", files=[("files", ("policy.txt", f"Policy Number: P-1\nPeriod of insurance: to midnight of {later}".encode()))]).json()[0]["document"]
    assert bill["due_on"] == (date.today() + timedelta(days=20)).isoformat() and bill["amount"] == "2486"
    assert bill["facts_method"] == "rules" and policy["expires_on"]

    upcoming = me.get("/api/upcoming").json()
    assert [(u["filename"], u["kind"], u["days_left"]) for u in upcoming] == [("bill.txt", "due", 20)]
    assert len(me.get("/api/upcoming", params={"days": 365}).json()) == 2

    fixed = me.patch(f"/api/documents/{policy['doc_id']}", json={"expires_on": "2031-01-31", "amount": "Rs. 1,000"}).json()
    assert fixed["expires_on"] == "2031-01-31" and fixed["amount"] == "1000" and fixed["facts_method"] == "person"
    assert me.patch(f"/api/documents/{policy['doc_id']}", json={"expires_on": "31/01/2031"}).status_code == 422

    # Re-reading older documents never overwrites what a person corrected.
    changes = refresh(store, FactsReader(prefer="rules"), apply=True)
    assert all(c["filename"] != "policy.txt" for c in changes)
    assert store.get(policy["doc_id"])["expires_on"] == "2031-01-31"
    assert DocumentStore(tmp_path / "index").get(policy["doc_id"])["expires_on"] == "2031-01-31"  # saved

    me.post("/api/users", json={"name": "Sunita", "password": PASSWORD + "2"})
    store.set_family_mode(False)  # private mode (Phase 12)
    from fastapi.testclient import TestClient

    sunita = TestClient(app)
    sunita.post("/api/auth/login", json={"name": "Sunita", "password": PASSWORD + "2"})
    assert sunita.get("/api/upcoming", params={"days": 365}).json() == []  # only your own documents
