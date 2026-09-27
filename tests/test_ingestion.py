import json

import pytest

from src.ingestion.chunker import RegexTokenizer, chunk_document
from src.ingestion.loader import Document, load_qrels, select_subset

TOK = RegexTokenizer()


def make_doc(n_words, doc_id="d1"):
    return Document(doc_id=doc_id, title="T", text=" ".join(f"w{i}" for i in range(n_words)))


def test_short_doc_is_single_chunk():
    chunks = chunk_document(make_doc(50), TOK, "beir/nq", chunk_size=100, overlap=20)
    assert len(chunks) == 1
    c = chunks[0]
    assert (c.chunk_id, c.chunk_index, c.num_chunks, c.n_tokens) == ("d1#0", 0, 1, 50)
    assert c.text == make_doc(50).text


def test_windows_overlap_and_cover_document():
    doc = make_doc(250)
    chunks = chunk_document(doc, TOK, "beir/nq", chunk_size=100, overlap=20)
    # windows start at 0, 80, 160; the 160 window reaches the end
    assert [c.n_tokens for c in chunks] == [100, 100, 90]
    assert all(c.num_chunks == 3 for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:]):
        shared = prev.text.split()[-20:]
        assert nxt.text.split()[:20] == shared
    assert chunks[0].start_char == 0 and chunks[-1].end_char == len(doc.text)
    for c in chunks:
        assert doc.text[c.start_char : c.end_char] == c.text


def test_short_tail_is_folded_into_previous_chunk():
    # windows 0-100, 80-180, 160-185 -> tail adds only 5 new tokens, gets folded
    chunks = chunk_document(make_doc(185), TOK, "beir/nq", chunk_size=100, overlap=20)
    assert [c.n_tokens for c in chunks] == [100, 105]


def test_rejects_bad_params():
    with pytest.raises(ValueError):
        chunk_document(make_doc(10), TOK, "s", chunk_size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_document(make_doc(10), TOK, "s", chunk_size=600, overlap=0)


def test_subset_keeps_all_relevant_docs(tmp_path):
    with open(tmp_path / "corpus.jsonl", "w") as f:
        for i in range(100):
            f.write(json.dumps({"_id": f"doc{i}", "title": "", "text": f"text {i}"}) + "\n")
    (tmp_path / "qrels").mkdir()
    (tmp_path / "qrels" / "test.tsv").write_text("query-id\tcorpus-id\tscore\nq1\tdoc7\t1\nq2\tdoc93\t1\n")
    qrels = load_qrels(tmp_path)
    docs = select_subset(tmp_path, qrels, max_docs=10, seed=0)
    ids = [d.doc_id for d in docs]
    assert len(ids) == 10 == len(set(ids))
    assert {"doc7", "doc93"} <= set(ids)
    with pytest.raises(ValueError):
        select_subset(tmp_path, qrels, max_docs=1)
