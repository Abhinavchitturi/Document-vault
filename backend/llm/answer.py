"""Write an answer from retrieved passages, citing which passages it used.

The model is told to answer only from the numbered passages, cite them as [n],
and say so when the answer isn't there. Today's date is included so that when
two versions of a document disagree (last year's policy and its renewal) it can
pick the one that is current.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

NOT_FOUND = "I couldn't find that in your documents."

SYSTEM_PROMPT = """You answer questions about a family's own documents: insurance policies, warranty cards, bills and property papers.

Rules:
- Use ONLY the numbered passages you are given. Never use outside knowledge or guess.
- Answer directly in one to three short sentences, for example: "Your bike insurance expires on 14 March 2027 [1]."
- After each fact, cite the passage it came from in square brackets, like [2].
- Today's date is {today}. If passages disagree because they are different years or versions of a document (for example an expired policy and its renewal), use the one that is current today.
- If the passages don't contain the answer, reply exactly: "{not_found}"
"""


class LLM(Protocol):
    name: str

    def generate(self, system: str, prompt: str) -> str: ...


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


def answer_question(question: str, passages: list[dict], llm: LLM, today: date | None = None) -> Answer:
    if not passages:
        return Answer(NOT_FOUND, found=False)
    system = SYSTEM_PROMPT.format(today=(today or date.today()).strftime("%d %B %Y"), not_found=NOT_FOUND)
    text = llm.generate(system, build_prompt(question, passages))
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(passages)})
    return Answer(text, cited, found=NOT_FOUND.rstrip(".").lower() not in text.lower())
