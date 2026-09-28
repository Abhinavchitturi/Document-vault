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
