"""The search model's first download, measured, so the apps can show a progress bar.

Only the files the vault uses are fetched (437 MB), not the whole model repository (which also holds ONNX and GGUF
copies, several GB), from a pinned commit. Afterwards everything runs offline.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass

from backend.embeddings.embedder import DEFAULT_MODEL, _hf_cache_dir

logger = logging.getLogger(__name__)

REVISION = "e58a8f756156a1293d763f17e3aae643474e9b8a"  # Snowflake/snowflake-arctic-embed-m-v1.5
FILES = {  # what sentence-transformers reads, with their sizes at that commit
    "model.safetensors": 435588776,
    "tokenizer.json": 711649,
    "README.md": 272657,
    "vocab.txt": 231508,
    "tokenizer_config.json": 1381,
    "config.json": 772,
    "special_tokens_map.json": 695,
    "modules.json": 350,
    "1_Pooling/config.json": 297,
    "config_sentence_transformers.json": 253,
    "sentence_bert_config.json": 108,
}
TOTAL = sum(FILES.values())


@dataclass
class Progress:
    state: str = "loading"  # downloading, loading (into memory), ready, failed
    done: int = 0  # bytes downloaded
    total: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


progress = Progress()


def _repo_dir(model_name: str):
    return _hf_cache_dir() / f"models--{model_name.replace('/', '--')}"


def revision_for(model_name: str) -> str | None:
    """The pinned commit for the default model (None for any other: its latest version)."""
    return REVISION if model_name == DEFAULT_MODEL else None


def downloaded(model_name: str = DEFAULT_MODEL) -> bool:
    snapshot = _repo_dir(model_name) / "snapshots" / REVISION
    return all((snapshot / name).is_file() and (snapshot / name).stat().st_size == size for name, size in FILES.items())


def _measured(model_name: str) -> int:
    """Bytes on disk so far: finished files, plus the one being downloaded."""
    snapshot = _repo_dir(model_name) / "snapshots" / REVISION
    done = sum(size for name, size in FILES.items() if (snapshot / name).is_file())
    blobs = _repo_dir(model_name) / "blobs"
    if blobs.is_dir():
        done += sum(p.stat().st_size for p in blobs.glob("*.incomplete"))
    return min(done, TOTAL)


def ensure(model_name: str = DEFAULT_MODEL) -> None:
    """Download the default search model if it isn't here yet, keeping `progress` up to date."""
    if model_name != DEFAULT_MODEL or downloaded(model_name):
        progress.state = "loading"
        return
    from huggingface_hub import hf_hub_download

    progress.state, progress.done, progress.total = "downloading", _measured(model_name), TOTAL
    logger.info("Downloading the search model (%d MB, once)", TOTAL // 1_000_000)
    finished = threading.Event()

    def measure():
        while not finished.wait(0.5):
            progress.done = _measured(model_name)

    threading.Thread(target=measure, name="measure-download", daemon=True).start()
    try:  # one at a time: parallel downloads hung on Windows (the small ones take seconds anyway)
        for name in sorted(FILES, key=FILES.get, reverse=True):  # the big one first: the bar moves at once
            hf_hub_download(model_name, name, revision=REVISION)
    finally:
        finished.set()
    progress.done, progress.state = TOTAL, "loading"
