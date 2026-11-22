"""Slow things (the search model, its tokenizer) made in a background thread, so the vault opens at once.

Loading the search model takes a while (torch, and on the first start a ~440 MB download), and nothing needs it
until someone searches, asks or adds a document. The vault opens straight away; the first use waits for it.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)


class InBackground:
    """Stands in for the object `make()` returns, made in a background thread. Attributes given as `known` are
    answered at once (the model's name); anything else waits until the object is there, then is passed through."""

    def __init__(self, what: str, make: Callable[[], Any], **known: Any):
        self.__dict__.update(known)
        self.__dict__.update(_what=what, _value=None, _error=None, _done=threading.Event())
        threading.Thread(target=self._make, args=(make,), name=f"load-{what}", daemon=True).start()

    def _make(self, make: Callable[[], Any]) -> None:
        try:
            self.__dict__["_value"] = make()
            logger.info("%s ready", self._what)
        except BaseException as e:  # noqa: BLE001 — raised again on use
            logger.error("%s didn't load: %s", self._what, e)
            self.__dict__["_error"] = e
        finally:
            self._done.set()

    @property
    def ready(self) -> bool:
        return self._done.is_set() and self._error is None

    def wait(self) -> Any:
        self._done.wait()
        if self._error is not None:
            raise RuntimeError(f"The {self._what} couldn't load: {self._error}") from self._error
        return self._value

    def __getattr__(self, name: str) -> Any:  # only for attributes not set above
        return getattr(self.wait(), name)
