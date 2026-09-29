"""Sort documents that were added before Phase 8, or re-sort after improving the sorter.

    python -m backend.classify.run              # show what would change, for every family member
    python -m backend.classify.run --apply      # make the changes

Documents whose type a person chose are never touched. Documents added before Phase 8 have
no record of who chose their type, so for those the type is only changed with --apply and
the suggestion is printed first.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import select

from backend.classify.classifier import REVIEW_BELOW, Classifier
from backend.database.schema import users

logger = logging.getLogger("classify")


def resort(store, classifier: Classifier, embed_passages, apply: bool) -> list[dict]:
    """Re-sort every automatically sorted document in the vault; returns what changed (or would)."""
    from backend.upload.ingest import passage_texts

    with store.engine.connect() as conn:
        user_ids = conn.execute(select(users.c.id)).scalars().all()
    report = []
    for user_id in user_ids:
        view = store.for_user(user_id)
        for doc in list(view.documents.values()):
            if doc.get("category_source") == "person":
                continue
            chunks = view.chunks_of(doc["doc_id"])
            text = "\n".join(c["text"] for c in sorted(chunks, key=lambda c: (c["page"], c["vid"])))
            decision = classifier.classify(doc["filename"], text)
            change = {"owner": view.owner, "filename": doc["filename"], "from": doc["category"],
                      "to": decision.category, "confidence": decision.confidence, "method": decision.method}
            moved = decision.category != doc["category"]
            if moved or doc.get("category_source") is None or doc.get("category_confidence") != decision.confidence:
                report.append(change)
                if apply:
                    vectors = None
                    if moved and chunks:
                        vectors = embed_passages(passage_texts([{**c, "category": decision.category} for c in chunks]))
                    view.update_category(doc["doc_id"], decision.category, vectors, source="auto",
                                         confidence=decision.confidence)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the changes (default: only show them)")
    parser.add_argument("--index-dir", default="data/index")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--no-llm", action="store_true", help="don't ask the local answer model when unsure")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from backend.database.db import DatabaseUnavailableError, database_url
    from backend.embeddings.embedder import DEFAULT_MODEL, Embedder
    from backend.llm.ollama import DEFAULT_MODEL as DEFAULT_LLM
    from backend.llm.ollama import OllamaLLM
    from backend.vector_store.store import DocumentStore

    try:
        store = DocumentStore(args.index_dir, database_url(args.database_url))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    embedder = Embedder(store.model or DEFAULT_MODEL)
    classifier = Classifier(embedder, None if args.no_llm else OllamaLLM(DEFAULT_LLM))
    report = resort(store, classifier, embedder.encode_passages, args.apply)
    if not report:
        print("Nothing to change.")
        return
    for r in report:
        arrow = f"{r['from']} -> {r['to']}" if r["from"] != r["to"] else f"{r['to']} (unchanged)"
        check = "  ⚠ check" if r["confidence"] < REVIEW_BELOW else ""
        print(f"{r['owner']:<12} {r['filename'][:48]:<48} {arrow:<26} {r['confidence']:>3}% {r['method']}{check}")
    print("\nApplied." if args.apply else "\nNothing changed yet. Run again with --apply to make these changes.")


if __name__ == "__main__":
    main()
