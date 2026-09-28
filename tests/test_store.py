import numpy as np
import pytest

from backend.chunker.chunker import RegexTokenizer
from backend.upload.ingest import EmptyDocumentError, ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder
from tests.pdf_helper import make_pdf

TOK = RegexTokenizer()


@pytest.fixture
def lib(tmp_path):
    store, emb = DocumentStore(tmp_path / "index"), FakeEmbedder()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "bike_insurance.md").write_text("Two-wheeler policy. Expires on 14 March 2027. Premium Rs 1,842.")
    (docs / "washing_machine_warranty.md").write_text("Front load washer. Motor warranty 10 years. Serial VFL7012.")
    (docs / "electricity_bill.md").write_text("Units consumed 312. Amount due Rs 2,486. Due date 18 September.")
    for p in sorted(docs.iterdir()):
        ingest_file(p, store, emb, TOK)
    return store, emb, tmp_path


def top(store, emb, question, **kw):
    return [c["filename"] for c, _ in store.search(emb.encode_queries([question])[0], **kw)]


def test_ingest_records_document_and_chunks(lib):
    store, _, _ = lib
    doc = next(d for d in store.documents.values() if d["filename"] == "bike_insurance.md")
    assert doc["category"] == "insurance"  # guessed from the text
    assert doc["pages"] == 1 and doc["num_chunks"] == 1
    assert (store.files_dir / doc["stored_as"]).exists()
    assert len(store.chunks) == store.num_vectors == 3


def test_search_finds_the_right_document_and_filters_by_category(lib):
    store, emb, _ = lib
    assert top(store, emb, "when does the bike insurance expire")[0] == "bike_insurance.md"
    assert top(store, emb, "washing machine motor warranty")[0] == "washing_machine_warranty.md"
    assert top(store, emb, "expire", category="bill") == ["electricity_bill.md"]


def test_same_file_twice_is_not_duplicated(lib):
    store, emb, tmp_path = lib
    result = ingest_file(tmp_path / "docs" / "bike_insurance.md", store, emb, TOK)
    assert not result.created and len(store.documents) == 3


def test_library_survives_restart_and_delete(lib):
    store, emb, tmp_path = lib
    reloaded = DocumentStore(tmp_path / "index")
    assert len(reloaded.documents) == 3 and reloaded.num_vectors == 3
    q = emb.encode_queries(["units consumed amount due"])[0]
    assert [(c["chunk_id"], round(s, 5)) for c, s in reloaded.search(q)] == [(c["chunk_id"], round(s, 5)) for c, s in store.search(q)]
    doc_id = next(d["doc_id"] for d in reloaded.documents.values() if d["filename"] == "electricity_bill.md")
    stored = reloaded.file_path(doc_id)
    assert reloaded.delete(doc_id)
    assert not stored.exists()
    again = DocumentStore(tmp_path / "index")
    assert len(again.documents) == 2 and len(again.chunks) == again.num_vectors == 2
    assert "electricity_bill.md" not in top(again, emb, "units consumed amount due")


def test_multi_page_pdf_keeps_page_numbers(tmp_path):
    store, emb = DocumentStore(tmp_path / "index"), FakeEmbedder()
    (tmp_path / "policy.pdf").write_bytes(make_pdf(["Schedule of cover", "Policy expires on 14 March 2027"]))
    ingest_file(tmp_path / "policy.pdf", store, emb, TOK, category="insurance")
    [(chunk, _)] = store.search(emb.encode_queries(["policy expires"])[0], top_k=1)
    assert chunk["page"] == 2 and "14 March 2027" in chunk["text"]


def test_empty_document_is_rejected(tmp_path):
    (tmp_path / "blank.txt").write_text("   \n  ")
    with pytest.raises(EmptyDocumentError):
        ingest_file(tmp_path / "blank.txt", DocumentStore(tmp_path / "index"), FakeEmbedder(), TOK)


def test_model_mismatch_is_refused_until_reembedded(lib):
    store, emb, _ = lib

    class Other(FakeEmbedder):
        model_name = "other/model"

    with pytest.raises(ValueError, match="re-embed"):
        store.add({"doc_id": "x", "category": "other"}, [], np.zeros((0, 256)), "other/model")
    store.reembed(lambda chunks: Other().encode_passages([c["text"] for c in chunks]), "other/model")
    assert store.model == "other/model" and store.num_vectors == 3


def test_search_can_return_several_chunks_of_one_document(tmp_path):
    """per_document=False is what the LLM gets: the best passages, even if from one document."""
    store, emb = DocumentStore(tmp_path / "index"), FakeEmbedder()
    (tmp_path / "policy.pdf").write_bytes(make_pdf(["Policy expires on 14 March 2027", "Policy premium Rs 1,842"]))
    (tmp_path / "bill.txt").write_text("Electricity bill due 18 September")
    ingest_file(tmp_path / "policy.pdf", store, emb, TOK, category="insurance")
    ingest_file(tmp_path / "bill.txt", store, emb, TOK, category="bill")
    q = emb.encode_queries(["policy expires premium"])[0]
    assert sorted(c["page"] for c, _ in store.search(q, top_k=2, per_document=False)) == [1, 2]
    assert len(store.search(q, top_k=2, per_document=True)) == 2  # policy + bill
