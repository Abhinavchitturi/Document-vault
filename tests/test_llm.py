from datetime import date

from backend.embeddings.embedder import prefer_offline
from backend.llm.answer import NOT_FOUND, answer_question, build_prompt
from tests.fakes import FakeLLM

PASSAGES = [
    {"filename": "Bike Insurance 2025.pdf", "category": "insurance", "page": 1, "text": "Period: 15 March 2025 to 14 March 2026."},
    {"filename": "Bike Insurance 2026.pdf", "category": "insurance", "page": 1, "text": "Period: 15 March 2026 to 14 March 2027."},
]


def test_prompt_numbers_passages_with_their_source():
    prompt = build_prompt("When does it expire?", PASSAGES)
    assert "[1] Bike Insurance 2025.pdf (insurance), page 1:\nPeriod: 15 March 2025" in prompt
    assert "[2] Bike Insurance 2026.pdf" in prompt and prompt.endswith("Question: When does it expire?")


def test_answer_parses_citations_and_passes_today():
    llm = FakeLLM("Your bike insurance expires on 14 March 2027 [2]. The older policy ended in 2026 [1][7].")
    answer = answer_question("When does my bike insurance expire?", PASSAGES, llm, today=date(2026, 9, 28))
    assert answer.citations == [1, 2]  # [7] doesn't exist and is ignored
    assert answer.found
    assert "28 September 2026" in llm.last_system


def test_not_found_is_reported_and_no_passages_skips_the_llm():
    assert not answer_question("Q?", PASSAGES, FakeLLM(NOT_FOUND)).found
    assert NOT_FOUND == "I'm not able to find that in your documents."
    assert not answer_question("Q?", PASSAGES, FakeLLM("Sorry, I couldn't find the premium [1].")).found
    llm = FakeLLM("should not be called")
    answer = answer_question("Q?", [], llm)
    assert not answer.found and llm.calls == 0


def test_prefer_offline_only_when_model_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert not prefer_offline("Snowflake/snowflake-arctic-embed-m-v1.5")
    (tmp_path / "models--Snowflake--snowflake-arctic-embed-m-v1.5" / "snapshots" / "abc123").mkdir(parents=True)
    assert prefer_offline("Snowflake/snowflake-arctic-embed-m-v1.5")
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_reasoning_models_thinking_is_removed():
    llm = FakeLLM("<think>The 2026 policy is current, so...</think>\nIt expires on 14 March 2027 [2].")
    assert answer_question("Q?", PASSAGES, llm).text == "It expires on 14 March 2027 [2]."


def test_embedding_prefixes():
    from backend.embeddings.embedder import DEFAULT_MODEL, model_prefixes

    query, passage = model_prefixes(DEFAULT_MODEL)
    assert DEFAULT_MODEL.startswith("Snowflake/") and query.startswith("Represent this sentence") and passage == ""
    assert model_prefixes("intfloat/e5-base-v2") == ("query: ", "passage: ")
    assert model_prefixes("some/other-model") == ("", "")


def test_streaming_hides_thinking_and_keeps_normal_text():
    from backend.llm.answer import strip_thinking

    pieces = ["<th", "ink>weighing the", " two policies</thi", "nk>\nIt expires", " on 14 March 2027 [2]."]
    assert "".join(strip_thinking(pieces)) == "It expires on 14 March 2027 [2]."
    assert "".join(strip_thinking(["Your ", "bill is ", "Rs. 2,486."])) == "Your bill is Rs. 2,486."
    assert "".join(strip_thinking(["<b>"])) == "<b>"
