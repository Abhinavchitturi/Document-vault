"""Test doubles: a deterministic embedder that needs no model download."""

import re
import zlib

import numpy as np

DIM = 256


class FakeEmbedder:
    """Bag-of-words hashing: texts sharing words get similar vectors."""

    model_name = "fake/bow"

    def __init__(self):
        self.passage_calls = 0

    def _vec(self, text):
        v = np.zeros(DIM, dtype=np.float32)
        for word in re.findall(r"\w+", text.lower()):
            v[zlib.crc32(word.encode()) % DIM] += 1
        return v / (np.linalg.norm(v) or 1)

    def encode_passages(self, texts):
        self.passage_calls += 1
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, DIM), np.float32)

    def encode_queries(self, texts):
        return np.stack([self._vec(t) for t in texts])


class FakeLLM:
    """Returns a canned answer and records what it was asked."""

    name = "fake/llm"

    def __init__(self, reply="The answer is in the first passage [1].", error=None):
        self.reply, self.error = reply, error
        self.calls, self.last_system, self.last_prompt = 0, None, None

    def generate(self, system, prompt, history=None):
        self.calls += 1
        self.last_system, self.last_prompt, self.last_history = system, prompt, history
        if self.error:
            raise self.error
        return self.reply

    def stream(self, system, prompt, history=None):
        text = self.generate(system, prompt, history)
        for i in range(0, len(text), 7):  # arrive in small pieces, like a real model
            yield text[i : i + 7]


PASSWORD = "correct horse battery"


def signed_in(app, name=None):
    """A test client that has set up the vault and is signed in as its first member."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    status = client.get("/api/auth/status").json()
    r = client.post("/api/auth/setup", json={"name": name or status["suggested_name"] or "tester", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client
