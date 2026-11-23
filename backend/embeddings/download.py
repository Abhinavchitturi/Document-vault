"""The search model's first download, measured, so the apps can show a progress bar.

Only the files the vault uses are fetched (437 MB), not the whole model repository (which also holds ONNX and GGUF
copies, several GB), from a pinned commit. Afterwards everything runs offline.

They are kept as plain files in ~/.document-vault/search-model (or $VAULT_SEARCH_MODEL_DIR), not in the Hugging Face
cache: that cache links each file to a blob, and Windows refused to follow those links in the process that had just
made them ("[WinError 448] ... untrusted mount point"), so the first start after the download failed every time.
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
tokenizer_here = threading.Event()  # tokenizer.json is on disk (or the download gave up): adding can start


def model_dir() -> Path:
    """Where the search model's files are kept, as plain files."""
    return Path(os.environ.get("VAULT_SEARCH_MODEL_DIR") or Path.home() / ".document-vault" / "search-model")


def _old_snapshot(model_name: str = DEFAULT_MODEL) -> Path:
    """Where versions before 1.0.7 kept it: the Hugging Face cache."""
    return _hf_cache_dir() / f"models--{model_name.replace('/', '--')}" / "snapshots" / REVISION


def _complete(folder: Path) -> bool:
    return all((folder / name).is_file() and (folder / name).stat().st_size == size for name, size in FILES.items())


def downloaded(model_name: str = DEFAULT_MODEL) -> bool:
    return _complete(model_dir())


def _measured(model_name: str = DEFAULT_MODEL) -> int:
    """Bytes on disk so far: finished files, plus the one being downloaded."""
    folder = model_dir()
    done = sum(size for name, size in FILES.items()
               if (folder / name).is_file() and (folder / name).stat().st_size == size)
    if folder.is_dir():
        done += sum(p.stat().st_size for p in folder.rglob("*.incomplete") if p.is_file())
    return min(done, TOTAL)


def _move_from_old_cache(model_name: str) -> bool:
    """An earlier version downloaded it into the Hugging Face cache: copy it over once (no second download),
    then remove the old copy."""
    old = _old_snapshot(model_name)
    if not _complete(old):
        return False
    folder = model_dir()
    try:
        for name in FILES:
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".copying")
            shutil.copyfile(old / name, part)
            os.replace(part, target)
    except OSError as e:
        logger.warning("Couldn't copy the search model from the old place (%s); downloading it instead", e)
        return False
    shutil.rmtree(old.parent.parent, ignore_errors=True)
    logger.info("Search model moved to %s", folder)
    return True


def ensure(model_name: str = DEFAULT_MODEL) -> Path:
    """The default search model's folder, downloaded first if it isn't here yet (keeping `progress` up to date)."""
    try:
        if downloaded(model_name) or _move_from_old_cache(model_name):
            progress.state = "loading"
            return model_dir()
        _download(model_name)
        return model_dir()
    finally:
        tokenizer_here.set()


def _download(model_name: str) -> None:
    from huggingface_hub import hf_hub_download

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
            if (folder / name).is_file() and (folder / name).stat().st_size == FILES[name]:
                continue
            hf_hub_download(model_name, name, revision=REVISION, local_dir=folder)
            if name == "tokenizer.json":
                tokenizer_here.set()
    finally:
        finished.set()
    if not downloaded(model_name):
        raise OSError("the search model's files didn't arrive complete")
    progress.done, progress.state = TOTAL, "loading"
