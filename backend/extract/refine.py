"""A closer look at each added document by the private AI, after it's in the vault.

Adding a document used to wait for the private AI twice (to read its dates and amounts, and to settle its type when the
quick readers weren't sure). On a computer without a fast graphics chip, or with little memory, that took minutes per
file. Now a document is filed at once by the quick readers (text patterns and the search model), and the private AI
reads it again here, one document at a time, when it's running. What a person changed by hand is never overwritten.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from backend.classify.classifier import Classifier
from backend.extract.facts import FactsReader

logger = logging.getLogger(__name__)

WAIT_FOR_AI = 15 * 60  # seconds a document waits for the private AI to finish starting, before it's left as it is


@dataclass
class _Item:
    view: object  # the uploader's view of the vault (DocumentStore)
    doc_id: str
    filename: str
    text: str


class Refiner:
    def __init__(self, llm, embedder, passage_texts):
        self.llm = llm
        self.classifier = Classifier(embedder, llm)
        self.facts = FactsReader(llm, prefer="llm")
        self.embedder, self.passage_texts = embedder, passage_texts
        self._queue: queue.Queue[_Item] = queue.Queue(maxsize=500)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def add(self, view, doc_id: str, filename: str, text: str) -> None:
        if self.llm is None or not hasattr(self.llm, "generate_json"):
            return
        try:
            self._queue.put_nowait(_Item(view, doc_id, filename, text))
        except queue.Full:
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="refine", daemon=True)
                self._thread.start()

    def _ready(self) -> bool:
        """Whether the private AI answers now, waiting a while if it's still starting. False: it isn't coming."""
        deadline = time.monotonic() + WAIT_FOR_AI
        while True:
            ready, _ = self.llm.status()
            if ready:
                return True
            server = getattr(self.llm, "server", None)
            starting = server is not None and server.server and server.model and server.exited() is None
            if not starting or time.monotonic() > deadline:
                return False
            time.sleep(5)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=60)
            except queue.Empty:
                return  # started again by the next document
            try:
                if self._ready():
                    self.refine(item)
            except Exception:  # noqa: BLE001 (the quick readers' result stays)
                logger.exception("The private AI couldn't take a closer look at %s", item.filename)

    def refine(self, item: _Item) -> None:
        doc = item.view.get(item.doc_id)
        if not doc or not doc.get("mine"):
            return  # deleted meanwhile
        if doc.get("category_source") == "auto":  # not chosen by a person
            decision = self.classifier.classify(item.filename, item.text)  # asks the AI only when it's unclear
            if decision.method == "assistant" and (decision.category, decision.confidence) != (
                    doc.get("category"), doc.get("category_confidence")):
                moved = decision.category != doc.get("category")
                chunks = [{**c, "category": decision.category} for c in item.view.chunks_of(item.doc_id)]
                vectors = self.embedder.encode_passages(self.passage_texts(chunks)) if moved and chunks else None
                item.view.update_category(item.doc_id, decision.category, vectors, source="auto",
                                          confidence=decision.confidence)
        doc = item.view.get(item.doc_id)
        if doc and doc.get("facts_method") != "person":  # not corrected by a person
            facts = self.facts.read(item.text)
            if facts.method == "llm":
                fields = facts.as_document_fields()
                fields.pop("facts_method", None)
                if doc.get("category") == "bill":
                    fields["expires_on"] = None  # a bill's billing period isn't an expiry
                item.view.update_facts(item.doc_id, fields, "llm")
        logger.info("The private AI took a closer look at %s", item.filename)
