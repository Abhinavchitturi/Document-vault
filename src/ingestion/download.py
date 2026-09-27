"""Download and unpack BEIR datasets.

BEIR hosts each dataset as a zip containing:
    corpus.jsonl   one {"_id", "title", "text"} per line
    queries.jsonl  one {"_id", "text"} per line
    qrels/<split>.tsv   query-id, corpus-id, score
"""

from __future__ import annotations

import logging
import shutil
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"


def download_beir_dataset(name: str = "nq", out_dir: str | Path = "data/raw") -> Path:
    """Download and unzip a BEIR dataset. Returns the dataset directory.

    Skips the download if the unpacked dataset already exists.
    """
    out_dir = Path(out_dir)
    dataset_dir = out_dir / name
    if (dataset_dir / "corpus.jsonl").exists():
        logger.info("Dataset already present at %s, skipping download", dataset_dir)
        return dataset_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{name}.zip"
    if not zip_path.exists():
        url = BEIR_URL.format(name=name)
        logger.info("Downloading %s -> %s", url, zip_path)
        tmp_path = zip_path.with_suffix(".zip.part")
        with urllib.request.urlopen(url) as resp, open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f, length=1 << 20)
        tmp_path.rename(zip_path)

    logger.info("Unzipping %s", zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(out_dir)
    return dataset_dir
