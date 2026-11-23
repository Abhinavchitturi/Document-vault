"""The search model's first download, measured, so the apps can show a progress bar.

Only the two files the vault uses are fetched (437 MB: the model in ONNX form, and its tokenizer), not the whole
model repository, from a pinned commit. Afterwards everything runs offline.

They are kept as plain files in ~/.document-vault/search-model (or $VAULT_SEARCH_MODEL_DIR), not in the Hugging Face
cache: that cache links each file to a blob, and Windows can refuse to follow those links ("[WinError 448] ...
untrusted mount point"), even to check that a file is there. Versions before 1.0.8 used that cache (and PyTorch's
copy of the model); it is removed once the new files are here, without looking inside it.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.embeddings.embedder import DEFAULT_MODEL, DEFAULT_REVISION, _hf_cache_dir, revision_for  # noqa: F401

logger = logging.getLogger(__name__)

REVISION = DEFAULT_REVISION  # Snowflake/snowflake-arctic-embed-m-v1.5
FILES = {  # with their sizes at that commit
    "onnx/model.onnx": 435939137,
    "tokenizer.json": 711649,
}
TOTAL = sum(FILES.values())
OLD_FILES = ["model.safetensors", "README.md", "vocab.txt", "tokenizer_config.json", "config.json",
             "special_tokens_map.json", "modules.json", "1_Pooling", "config_sentence_transformers.json",
             "sentence_bert_config.json"]  # 1.0.7's PyTorch copy, in the same folder


@dataclass
class Progress:
    state: str = "loading"  # downloading, loading (into memory), ready, failed
    done: int = 0  # bytes downloaded
    total: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


progress = Progress()
tokenizer_here = threading.Event()  # tokenizer.json is on disk (or the download gave up): adding can start


def model_dir() -> Path:
    """Where the search model's files are kept, as plain files."""
    return Path(os.environ.get("VAULT_SEARCH_MODEL_DIR") or Path.home() / ".document-vault" / "search-model")


def _has(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:  # a link Windows won't follow, a file in use: not usable, so not here
        return False


def downloaded(model_name: str = DEFAULT_MODEL) -> bool:
    return all(_has(model_dir() / name, size) for name, size in FILES.items())


def _measured(model_name: str = DEFAULT_MODEL) -> int:
    """Bytes on disk so far: finished files, plus the one being downloaded."""
    folder = model_dir()
    done = sum(size for name, size in FILES.items() if _has(folder / name, size))
    try:
        if folder.is_dir():
            done += sum(p.stat().st_size for p in folder.rglob("*.incomplete") if p.is_file())
    except OSError:
        pass
    return min(done, TOTAL)


def _remove_old_copies(model_name: str) -> None:
    """Earlier versions' copies (PyTorch's, in the Hugging Face cache or in this folder): 437 MB each, unused now.
    Links are removed, never followed."""
    shutil.rmtree(_hf_cache_dir() / f"models--{model_name.replace('/', '--')}", ignore_errors=True)
    for name in OLD_FILES:
        path = model_dir() / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def ensure(model_name: str = DEFAULT_MODEL) -> Path:
    """The default search model's folder, downloaded first if it isn't here yet (keeping `progress` up to date)."""
    try:
        _remove_old_copies(model_name)  # first: the download below never meets them
        if not downloaded(model_name):
            _download(model_name)
        progress.state = "loading"
        return model_dir()
    finally:
        tokenizer_here.set()


def _download(model_name: str) -> None:
    for setting in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):  # this one download may use the internet
        os.environ.pop(setting, None)
    from huggingface_hub import constants, hf_hub_download

    constants.HF_HUB_OFFLINE = False  # read when huggingface_hub was imported

    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    progress.state, progress.done, progress.total = "downloading", _measured(model_name), TOTAL
    logger.info("Downloading the search model (%d MB, once)", TOTAL // 1_000_000)
    finished = threading.Event()

    def measure():
        while not finished.wait(0.5):
            progress.done = _measured(model_name)

    threading.Thread(target=measure, name="measure-download", daemon=True).start()
    try:  # one at a time: parallel downloads hung on Windows
        # the small ones first (a second or two): the tokenizer is there at once, so adding documents can start
        for name in sorted(FILES, key=FILES.get):
            if _has(folder / name, FILES[name]):
                continue
            hf_hub_download(model_name, name, revision=REVISION, local_dir=folder)
            if name == "tokenizer.json":
                tokenizer_here.set()
    finally:
        finished.set()
    if not downloaded(model_name):
        raise OSError("the search model's files didn't arrive complete")
    progress.done, progress.state = TOTAL, "loading"
