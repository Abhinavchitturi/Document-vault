"""Read the key facts (expiry, due date, amount, reference, issuer) of documents added before
Phase 9, or re-read them after installing Needle.

    python -m backend.extract.run            # show what would be filled in, for every family member
    python -m backend.extract.run --apply    # save it

Facts a person corrected are never overwritten.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from backend.database.schema import users
from backend.extract.facts import FIELDS, FactsReader


def refresh(store, reader: FactsReader, apply: bool) -> list[dict]:
    with store.engine.connect() as conn:
        user_ids = conn.execute(select(users.c.id)).scalars().all()
    report = []
    for user_id in user_ids:
        view = store.for_user(user_id)
        for doc in list(view.documents.values()):
            if doc.get("facts_method") == "person":
                continue
            chunks = sorted(view.chunks_of(doc["doc_id"]), key=lambda c: (c["page"], c["vid"]))
            facts = reader.read("\n".join(c["text"] for c in chunks)).as_document_fields()
            before = {f: doc.get(f) for f in FIELDS}
            after = {f: facts[f] for f in FIELDS}
            if before != after or doc.get("facts_method") != facts["facts_method"]:
                report.append({"owner": view.owner, "filename": doc["filename"], "before": before, "after": after,
                               "method": facts["facts_method"]})
                if apply:
                    view.update_facts(doc["doc_id"], facts, facts["facts_method"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--index-dir", default="data/index")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reader", choices=["auto", "needle", "ollama", "rules"], default="auto")
    args = parser.parse_args()

    from backend.database.db import DatabaseUnavailableError, database_url
    from backend.llm.ollama import DEFAULT_MODEL, OllamaLLM
    from backend.vector_store.store import DocumentStore

    try:
        store = DocumentStore(args.index_dir, database_url(args.database_url))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    reader = FactsReader(OllamaLLM(DEFAULT_MODEL), prefer=args.reader)
    report = refresh(store, reader, args.apply)
    if not report:
        print("Nothing to change.")
        return
    for r in report:
        shown = ", ".join(f"{k.replace('_on', '')}: {v}" for k, v in r["after"].items() if v) or "nothing found"
        print(f"{r['owner']:<12} {r['filename'][:44]:<44} {shown}  [{r['method']}]")
    print("\nSaved." if args.apply else "\nNothing saved yet. Run again with --apply to save these.")


if __name__ == "__main__":
    main()
