"""Download the local AI once: llama.cpp's server and the answer model (about 4.9 GB).

    python -m backend.llm.setup            # the installers run this
    python -m backend.llm.setup --check    # is it all there? (exit code 0 / 1)

Both files come from pinned versions (llamacpp.BUILD, llamacpp.MODEL) and are checked against their
published SHA-256 before use. An interrupted model download continues where it stopped the next time.
Everything goes in ~/.document-vault/llm (or $VAULT_LLM_DIR); deleting that folder removes it.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

from backend.llm import llamacpp

CHUNK = 1 << 20


class SetupError(RuntimeError):
    """A download failed or didn't match its checksum; the message says what to do."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK * 8), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, size: int | None = None, say=print, opener=urllib.request.urlopen) -> Path:
    """Fetch url into target, continuing a `.partial` file left by an interrupted try."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    have = partial.stat().st_size if partial.exists() else 0
    if size is not None and have > size:  # not ours, or a different file: start again
        partial.unlink()
        have = 0
    request = urllib.request.Request(url, headers={"User-Agent": "document-vault-setup"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    with opener(request, timeout=60) as response:
        if have and getattr(response, "status", 200) != 206:  # the server ignored the range: start over
            have = 0
        total = size or (have + int(response.headers.get("Content-Length") or 0)) or None
        shown, started = -1, time.monotonic()
        with open(partial, "ab" if have else "wb") as out:
            done = have
            while block := response.read(CHUNK):
                out.write(block)
                done += len(block)
                if total and int(done * 100 / total) != shown:
                    shown = int(done * 100 / total)
                    speed = (done - have) / max(time.monotonic() - started, 0.001) / 1e6
                    amount = f"{total / 1e9:.1f} GB" if total >= 1e9 else f"{total / 1e6:.0f} MB"
                    say(f"\r  {target.name}: {shown}% of {amount} ({speed:.0f} MB/s)  ", end="")
    say("")
    partial.replace(target)
    return target


def _extract(archive: Path, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():  # nothing may land outside the folder
                if not (folder / name).resolve().is_relative_to(folder.resolve()):
                    raise SetupError(f"Unsafe path in {archive.name}: {name}")
            z.extractall(folder)
    else:
        with tarfile.open(archive) as t:
            t.extractall(folder, filter="data")  # refuses absolute paths, '..' and links out of the folder


def install_server(say=print, opener=urllib.request.urlopen) -> Path:
    found = llamacpp.server_path()
    if found:
        return found
    choice = llamacpp.binary_for_this_computer()
    if not choice:
        raise SetupError(f"There is no llama.cpp build here for {sys.platform} on this processor. "
                         "Install Ollama instead (https://ollama.com): the vault uses it when llama.cpp isn't there.")
    name, expected = choice
    say(f"Downloading llama.cpp {llamacpp.BUILD} ({name})")
    archive = download(f"{llamacpp.RELEASES}/{name}", llamacpp.llm_dir() / "downloads" / name, say=say, opener=opener)
    if sha256_of(archive) != expected:
        archive.unlink()
        raise SetupError(f"{name} did not match its published checksum, so it was deleted. Run the setup again.")
    folder = llamacpp.llm_dir() / "bin" / llamacpp.BUILD
    shutil.rmtree(folder, ignore_errors=True)
    _extract(archive, folder)
    archive.unlink()
    found = llamacpp.server_path()
    if not found:
        raise SetupError(f"{name} has no llama-server in it.")
    if sys.platform != "win32":
        found.chmod(found.stat().st_mode | 0o111)
    return found


def install_model(say=print, opener=urllib.request.urlopen) -> Path:
    found = llamacpp.model_path()
    if found:
        return found
    model = llamacpp.MODEL
    say(f"Downloading the answer model {model['label']} ({model['size'] / 1e9:.1f} GB, once)")
    target = llamacpp.llm_dir() / "models" / model["file"]
    pending = target.with_name(target.name + ".unchecked")
    download(model["url"], pending, size=model["size"], say=say, opener=opener)
    say("  Checking the download…")
    if pending.stat().st_size != model["size"] or sha256_of(pending) != model["sha256"]:
        pending.unlink()
        raise SetupError("The model download didn't match its published checksum, so it was deleted. Run the setup again.")
    pending.replace(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report whether everything is downloaded")
    args = parser.parse_args()
    if args.check:
        print(f"llama.cpp: {llamacpp.server_path() or 'not downloaded'}")
        print(f"model:     {llamacpp.model_path() or 'not downloaded'}")
        raise SystemExit(0 if llamacpp.installed() else 1)
    try:
        server = install_server()
        model = install_model()
    except (SetupError, OSError) as e:
        raise SystemExit(f"Setup of the local AI stopped: {e}") from e
    print(f"The local AI is ready: {server.name} with {model.name} in {llamacpp.llm_dir()}")


if __name__ == "__main__":
    main()
