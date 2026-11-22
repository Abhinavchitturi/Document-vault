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
