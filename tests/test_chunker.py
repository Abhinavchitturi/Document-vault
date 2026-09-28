import pytest

from backend.chunker.chunker import RegexTokenizer, chunk_text

TOK = RegexTokenizer()


def words(n):
    return " ".join(f"w{i}" for i in range(n))


def test_short_text_is_one_chunk():
    text = words(50)
    [span] = chunk_text(text, TOK, chunk_size=100, overlap=20)
    assert (span.start_char, span.end_char, span.n_tokens) == (0, len(text), 50)


def test_windows_overlap_and_cover_the_text():
    text = words(250)
    spans = chunk_text(text, TOK, chunk_size=100, overlap=20)
    assert [s.n_tokens for s in spans] == [100, 100, 90]
    chunks = [text[s.start_char : s.end_char] for s in spans]
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.split()[:20] == prev.split()[-20:]
    assert spans[0].start_char == 0 and spans[-1].end_char == len(text)


def test_short_tail_is_folded_into_previous_chunk():
    assert [s.n_tokens for s in chunk_text(words(185), TOK, chunk_size=100, overlap=20)] == [100, 105]


class PieceTokenizer:
    """Splits words into 3-char pieces, like WordPiece does for rare words."""

    name = "pieces"

    def spans(self, text):
        return [(s, min(s + 3, e)) for (w0, e) in TOK.spans(text) for s in range(w0, e, 3)]


def test_chunks_never_split_a_word():
    text = " ".join(f"word{i:05d}" for i in range(120))
    spans = chunk_text(text, PieceTokenizer(), chunk_size=100, overlap=20)
    assert len(spans) > 1
    vocab = set(text.split())
    for s in spans:
        assert set(text[s.start_char : s.end_char].split()) <= vocab
        assert s.n_tokens <= 100


def test_rejects_bad_params():
    with pytest.raises(ValueError):
        chunk_text("a b", TOK, chunk_size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_text("a b", TOK, chunk_size=600, overlap=0)
