"""Phase 8: sorting documents into types."""

from pathlib import Path

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.classify.classifier import REVIEW_BELOW, Classifier, keyword_scores
from backend.classify.run import resort
from backend.eval.classify_eval import evaluate, load_samples
from backend.upload.ingest import ingest_file, passage_texts
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "documents"
BILL = "Consumer number 4471. Units consumed 312. Amount due Rs. 2,486. Due date 18 September 2026. Late fee Rs. 50."


class SorterLLM:
    """Stands in for Ollama's JSON answers."""

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    def generate_json(self, system, prompt, schema):
        self.calls += 1
        assert schema["properties"]["category"]["enum"] == ["insurance", "warranty", "bill", "property", "identity", "other"]
        if self.fail:
            raise ConnectionError("Ollama is off")
        return self.answer


def test_clear_documents_are_sorted_without_the_assistant():
    llm = SorterLLM({"category": "other", "confidence": "high"})
    d = Classifier(FakeEmbedder(), llm).classify("scan001.jpg", BILL)
    assert d.category == "bill" and d.confidence >= REVIEW_BELOW and not d.needs_review
    assert llm.calls == 0  # keywords and meaning agreed clearly: no need to ask


def test_unclear_documents_ask_the_assistant_and_fall_back_when_it_is_off():
    text = "Thank you for choosing us. Please keep this page safe. Reference 2231094."
    asked = Classifier(FakeEmbedder(), SorterLLM({"category": "warranty", "confidence": "medium"})).classify("x.pdf", text)
    assert (asked.category, asked.confidence, asked.method) == ("warranty", 70, "assistant")

    off = Classifier(FakeEmbedder(), SorterLLM(fail=True)).classify("x.pdf", text)
    assert off.method != "assistant" and off.category in {"insurance", "warranty", "bill", "property", "identity", "other"}
    assert Classifier(FakeEmbedder(), SorterLLM({"category": "cars", "confidence": "high"})).classify("x.pdf", text).method != "assistant"


def test_no_evidence_leans_to_other():
    scores = keyword_scores("IMG_2211.jpg", "Hello there")
    assert max(scores, key=scores.get) == "other"


def test_samples_are_sorted_correctly_with_names_hidden():
    results = evaluate(load_samples(SAMPLES), FakeEmbedder(), hide_names=True)
    assert results["keywords"]["accuracy"] == 1.0
    assert results["keywords+meaning"]["accuracy"] == 1.0


def test_uploads_record_how_the_type_was_chosen(tmp_path):
    client = signed_in(create_app(DocumentStore(tmp_path / "index"), FakeEmbedder(), RegexTokenizer()))
    auto = client.post("/api/documents", files=[("files", ("scan.txt", BILL.encode()))]).json()[0]["document"]
    assert (auto["category"], auto["category_source"], auto["needs_review"]) == ("bill", "auto", False)
    assert auto["category_confidence"] >= REVIEW_BELOW
    chosen = client.post("/api/documents", files=[("files", ("x.txt", b"Anything at all here"))],
                         data={"category": "property"}).json()[0]["document"]
    assert (chosen["category"], chosen["category_source"], chosen["category_confidence"]) == ("property", "person", 100)
    fixed = client.patch(f"/api/documents/{auto['doc_id']}", json={"category": "other"}).json()
    assert fixed["category_source"] == "person" and not fixed["needs_review"]


def test_resort_fixes_automatic_types_and_leaves_chosen_ones(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    emb, tok = FakeEmbedder(), RegexTokenizer()
    (tmp_path / "bill.txt").write_text(BILL)
    (tmp_path / "mine.txt").write_text(BILL + " Filed by hand.")
    old = ingest_file(tmp_path / "bill.txt", store, emb, tok, "warranty").document  # an old, wrong automatic guess
    store.update_category(old["doc_id"], "warranty", source="auto", confidence=30)
    kept = ingest_file(tmp_path / "mine.txt", store, emb, tok, "insurance", filename="mine2.txt").document
    other = store.for_user(store._ensure_user("sunita"))
    (tmp_path / "hers.txt").write_text(BILL + " Hers.")
    ingest_file(tmp_path / "hers.txt", other, emb, tok, "other")
    other.update_category(other.own_documents()[0]["doc_id"], "other", source="auto", confidence=20)

    embed = lambda texts: emb.encode_passages(texts)  # noqa: E731
    preview = resort(store, Classifier(emb), embed, apply=False)
    assert {(r["owner"], r["from"], r["to"]) for r in preview} == {("abhinav", "warranty", "bill"), ("sunita", "other", "bill")}
    assert store.get(old["doc_id"])["category"] == "warranty"  # only shown, not changed

    resort(store, Classifier(emb), embed, apply=True)
    assert store.get(old["doc_id"])["category"] == "bill"
    assert store.get(kept["doc_id"])["category"] == "insurance"  # a person chose it
    assert all(c["category"] == "bill" for c in store.chunks_of(old["doc_id"]))
    assert passage_texts(store.chunks_of(old["doc_id"]))[0].startswith("bill (bill)")
