"""Write an answer from retrieved passages, citing which passages it used.

The model is told to answer only from the numbered passages, cite them as [n],
and say so when the answer isn't there. Today's date is included so that when
two versions of a document disagree (last year's policy and its renewal) it can
pick the one that is current.

Two ways to call it:
- answer_question(): the whole answer at once (evaluation, scripts)
- stream_answer():   piece by piece as the model writes, for the chat page;
                     finish with finalize() to get the citations
Both accept earlier chat turns (`history`) so follow-ups like "and when is it
due?" make sense.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator, Protocol

NOT_FOUND = "I couldn't find that in your documents."
MAX_HISTORY_MESSAGES = 6  # last three question/answer pairs

SYSTEM_PROMPT = """You are the family's document assistant. You answer questions about the family's own documents: insurance policies, warranty cards, bills and property papers.

Rules:
- Use ONLY the numbered passages in the latest message. Never use outside knowledge or guess.
- Answer like a helpful assistant in a chat: directly, in one to three short sentences, for example "Your bike insurance expires on 14 March 2027 [1]." Use a short bullet list only when several items are asked for.
- After each fact, cite the passage it came from in square brackets, like [2].
- Earlier messages are the conversation so far; use them to understand follow-up questions, but take facts only from the passages.
- Today's date is {today}. If passages disagree because they are different years or versions of a document (for example an expired policy and its renewal), use the one that is current today.
- If the passages don't contain the answer, reply exactly: "{not_found}"
"""


class LLM(Protocol):
    name: str

    def generate(self, system: str, prompt: str, history: list[dict] | None = None) -> str: ...


@dataclass
class Answer:
    text: str
    citations: list[int] = field(default_factory=list)  # 1-based passage numbers the answer cites
    found: bool = True


def build_prompt(question: str, passages: list[dict]) -> str:
    blocks = [
        f"[{i}] {p['filename']} ({p['category']}), page {p['page']}:\n{p['text']}"
        for i, p in enumerate(passages, start=1)
    ]
    return "Passages:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"


def clean_history(history: list[dict] | None) -> list[dict]:
    """Keep the last few turns; drop old citation numbers, which point at earlier passage lists."""
    turns = [t for t in (history or []) if t.get("role") in ("user", "assistant") and t.get("content")]
    return [
        {"role": t["role"], "content": re.sub(r"\s*\[\d+\]", "", t["content"]).strip()}
        for t in turns[-MAX_HISTORY_MESSAGES:]
    ]


def _system(today: date | None) -> str:
    return SYSTEM_PROMPT.format(today=(today or date.today()).strftime("%d %B %Y"), not_found=NOT_FOUND)


def strip_thinking(pieces: Iterable[str]) -> Iterator[str]:
    """Drop a leading <think>...</think> block (reasoning models) from a stream of text pieces."""
    buffer, state = "", "start"  # start -> (thinking ->) passthrough
    for piece in pieces:
        if state == "passthrough":
            yield piece
            continue
        buffer += piece
        if state == "start":
            head = buffer.lstrip()
            if head.startswith("<think>"):
                state = "thinking"
            elif len(head) >= len("<think>") or not "<think>".startswith(head):
                state = "passthrough"
                yield buffer
                buffer = ""
                continue
        if state == "thinking" and "</think>" in buffer:
            state = "passthrough"
            rest = buffer.split("</think>", 1)[1].lstrip()
            buffer = ""
            if rest:
                yield rest
    if state == "start" and buffer:
        yield buffer


def finalize(text: str, num_passages: int) -> Answer:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= num_passages})
    return Answer(text, cited, found=NOT_FOUND.rstrip(".").lower() not in text.lower())


def answer_question(
    question: str, passages: list[dict], llm: LLM, today: date | None = None, history: list[dict] | None = None
) -> Answer:
    if not passages:
        return Answer(NOT_FOUND, found=False)
    text = llm.generate(_system(today), build_prompt(question, passages), clean_history(history))
    return finalize(text, len(passages))


def stream_answer(
    question: str, passages: list[dict], llm, today: date | None = None, history: list[dict] | None = None
) -> Iterator[str]:
    """Yield the answer as the model writes it. Join the pieces and call finalize() for citations."""
    if not passages:
        yield NOT_FOUND
        return
    yield from strip_thinking(llm.stream(_system(today), build_prompt(question, passages), clean_history(history)))
