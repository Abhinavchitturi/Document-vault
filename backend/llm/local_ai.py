"""The apps' one-click "Download the AI" button: downloads llama.cpp and the answer model in the background (the
same pinned, checksum-checked files as `python -m backend.llm.setup`), shows how far it is, and starts the answer
model when it's done, without restarting the vault."""

from __future__ import annotations

import logging
import threading

from backend.llm import llamacpp, setup

logger = logging.getLogger(__name__)


class LocalAI:
    def __init__(self, server: llamacpp.LlamaServer):
        self.server = server
        self._lock = threading.Lock()
        self._state = {"state": "idle", "step": None, "done": 0, "total": 0, "error": None}

    def downloadable(self) -> bool:
        """Is there something to download here (and a build of llama.cpp for this computer)?"""
        return llamacpp.binary_for_this_computer() is not None and not llamacpp.installed()

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start_download(self) -> dict:
        with self._lock:
            if self._state["state"] == "downloading":
                return dict(self._state)
            self._state = {"state": "downloading", "step": "llama.cpp", "done": 0, "total": 0, "error": None}
        threading.Thread(target=self._run, name="download-ai", daemon=True).start()
        return self.status()

    def start_automatically(self, after=None) -> None:
        """A computer that keeps the vault can't answer without the local AI, so the apps' engine downloads it by
        itself (in the background, after `after()` returns: the search model first). Not without room for it."""
        def run():
            if after is not None:
                try:
                    after()
                except Exception:  # noqa: BLE001 (the search model's own problem, shown on its own)
                    pass
            if not self.downloadable():
                return
            if (problem := self.too_little_space()) is not None:
                with self._lock:
                    self._state.update(state="failed", error=problem)
                logger.warning("Not downloading the local AI: %s", problem)
                return
            logger.info("Downloading the local AI by itself (about %d GB, once)", llamacpp.MODEL["size"] // 10**9)
            self.start_download()

        threading.Thread(target=run, name="download-ai-soon", daemon=True).start()

    @staticmethod
    def too_little_space() -> str | None:
        """Why there isn't room for the model (with 2 GB to spare), or None."""
        import shutil

        folder = llamacpp.llm_dir()
        here = folder
        while not here.exists():
            here = here.parent
        partial = folder / "models" / (llamacpp.MODEL["file"] + ".partial")
        needed = llamacpp.MODEL["size"] - (partial.stat().st_size if partial.exists() else 0) + 2 * 10**9
        free = shutil.disk_usage(here).free
        if free >= needed:
            return None
        return (f"There isn't enough free space for the local AI: it needs about {needed / 10**9:.0f} GB and this "
                f"computer has {free / 10**9:.1f} GB free. Free some space, then download it.")

    def _progress(self, step: str):
        def update(done: int, total: int) -> None:
            with self._lock:
                self._state.update(step=step, done=done, total=total)
        return update

    def _run(self) -> None:
        quiet = lambda *args, **kwargs: None  # noqa: E731 (progress goes to the apps, not a terminal)
        try:
            setup.install_server(say=quiet, progress=self._progress("llama.cpp"))
            with self._lock:
                self._state.update(step="model", done=0, total=llamacpp.MODEL["size"])
            setup.install_model(say=quiet, progress=self._progress("model"))
            self.server.server, self.server.model = llamacpp.server_path(), llamacpp.model_path()
            self.server.start()  # answers work from now on, no restart needed
            with self._lock:
                self._state.update(state="done", step=None, error=None)
            logger.info("The local AI was downloaded from the app and started")
        except Exception as e:  # noqa: BLE001 (shown in the app; running again continues where it stopped)
            logger.warning("Downloading the local AI stopped: %s", e)
            with self._lock:
                self._state.update(state="failed", error=f"{e} Try again: the download continues where it stopped.")
