"""Turn chunks and queries into normalized embedding vectors.

Default: Snowflake Arctic Embed M v1.5 (Snowflake, US; Apache 2.0; 109M params,
768-dim, ~440 MB), run through ONNX Runtime. The vault avoids models from Chinese developers, so BGE is out.

Arctic Embed and e5 are asymmetric retrievers: queries and passages are encoded
with different prefixes, and leaving the query prefix off measurably hurts recall.
Vectors are L2-normalized so cosine similarity is a plain dot product.

The model runs on this computer. It is downloaded from Hugging Face once; after
that `prefer_offline` switches the Hugging Face libraries to offline mode so
starting the vault makes no network requests at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

DEFAULT_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"
DEFAULT_REVISION = "e58a8f756156a1293d763f17e3aae643474e9b8a"  # its pinned version (backend/embeddings/download.py)


def revision_for(model_name: str) -> str | None:
    """The pinned commit for the default model (None for any other: its latest version)."""
    return DEFAULT_REVISION if model_name == DEFAULT_MODEL else None

# Prefixes recommended by each model's authors for query -> passage retrieval.
_PREFIXES = {
    "arctic-embed": ("Represent this sentence for searching relevant passages: ", ""),
    "e5": ("query: ", "passage: "),
}


def model_prefixes(model_name: str) -> tuple[str, str]:
    """Return (query_prefix, passage_prefix) for a model name."""
    name = model_name.lower()
    for family, prefixes in _PREFIXES.items():
        if family in name:
            return prefixes
    return "", ""


def format_passage(title: str, text: str, with_title: bool = True) -> str:
    """Text we embed for a chunk: its title (document name and category), then the
    text. Page two of a policy rarely repeats what the policy is for."""
    return f"{title} {text}" if with_title and title else text


def _hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def prefer_offline(model_name: str) -> bool:
    """If `model_name` is already downloaded, forbid Hugging Face network access.

    Must run before transformers / sentence-transformers are imported, since they
    read these settings at import time. Returns True if offline mode was enabled.
    """
    snapshots = _hf_cache_dir() / f"models--{model_name.replace('/', '--')}" / "snapshots"
    if snapshots.is_dir() and any(snapshots.iterdir()):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


class OnnxModel:
    """The default search model through ONNX Runtime: its ONNX file and tokenizer.json, CLS pooling (what the
    model's sentence-transformers setup does), so its vectors match PyTorch's (cosine 0.99999+)."""

    def __init__(self, folder: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        options.log_severity_level = 3  # errors only
        self.session = ort.InferenceSession(str(folder / "onnx" / "model.onnx"), options,
                                            providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.tokenizer.enable_truncation(512)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

    def encode(self, texts: list[str], batch_size: int = 8, normalize_embeddings: bool = True,
               convert_to_numpy: bool = True, show_progress_bar: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # alike lengths together: less padding
        parts = []
        for start in range(0, len(order), batch_size):
            batch = [texts[i] for i in order[start:start + batch_size]]
            enc = self.tokenizer.encode_batch(batch)
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                    "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
            hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            parts.append(hidden[:, 0])  # the [CLS] token
        vecs = np.concatenate(parts).astype(np.float32)
        out = np.empty_like(vecs)
        out[order] = vecs
        if normalize_embeddings:
            out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
        return out


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None, batch_size: int = 8,
                 revision: str | None = None):
        self.model_name = model_name
        self.batch_size = batch_size
        if model_name == DEFAULT_MODEL:  # the pinned files, downloaded once (every caller: the apps, the scripts)
            from backend.embeddings import download

            self.model = OnnxModel(download.ensure(model_name))
        else:  # another model, for experiments: needs `pip install sentence-transformers`
            prefer_offline(model_name)
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(model_name, device=device, revision=revision or revision_for(model_name))
        self.query_prefix, self.passage_prefix = model_prefixes(model_name)
    def _encode(self, texts: list[str], prefix: str) -> np.ndarray:
        vecs = self.model.encode(
            [prefix + t for t in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > self.batch_size,
        )
        return vecs.astype(np.float32)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, self.passage_prefix)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, self.query_prefix)
