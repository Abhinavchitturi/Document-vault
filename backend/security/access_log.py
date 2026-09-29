"""Access log (Phase 11): who signed in, opened, added, changed or removed what, and when.

Kept in the database (table access_log), a year at most. Details that may name a document are
encrypted like the rest of the vault. The admin sees everyone's entries in Settings → Activity;
everyone else sees their own, so a person can check nobody else used their account or phone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select

from backend.database.schema import access_log
from backend.security.crypto import Cipher, DecryptError

logger = logging.getLogger(__name__)

KEEP_DAYS = 365

# What a person reads in Settings → Activity.
ACTIONS = {
    "sign_in": "Signed in",
    "sign_in_passkey": "Signed in with a passkey",
    "sign_in_failed": "Failed sign-in",
    "sign_out": "Signed out",
    "password_changed": "Changed their password",
    "password_reset": "Reset a family member's password",
    "passkey_added": "Added a passkey",
    "passkey_removed": "Removed a passkey",
    "member_added": "Added a family member",
    "phone_paired": "Paired a phone",
    "phone_removed": "Removed a phone",
    "document_added": "Added a document",
    "document_opened": "Opened a document",
    "document_changed": "Changed a document's details",
    "document_deleted": "Removed a document",
    "backup_made": "Made an encrypted backup",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AccessLog:
    def __init__(self, engine, cipher: Cipher):
        self.engine = engine
        self.cipher = cipher
        self._last_prune: datetime | None = None

    def record(self, action: str, user: dict | None = None, *, user_name: str | None = None,
               document_id: str | None = None, detail: str | None = None, client: str = "", ok: bool = True) -> None:
        """Never lets logging break the request it describes."""
        try:
            via = (user or {}).get("device_name") or ("phone" if (user or {}).get("device_id") else "web")
            with self.engine.begin() as conn:
                conn.execute(insert(access_log).values(
                    at=_now(), user_id=(user or {}).get("id"), user_name=(user_name or (user or {}).get("name") or "")[:100],
                    via=via[:120], action=action, document_id=document_id,
                    detail=self.cipher.seal_text(detail[:500]) if detail else None, client=(client or "")[:64], ok=ok))
            self._prune()
        except Exception:  # noqa: BLE001
            logger.exception("could not write the access log")

    def _prune(self) -> None:
        now = _now()
        if self._last_prune and now - self._last_prune < timedelta(days=1):
            return
        self._last_prune = now
        with self.engine.begin() as conn:
            conn.execute(delete(access_log).where(access_log.c.at < now - timedelta(days=KEEP_DAYS)))

    def entries(self, user_id: int | None = None, limit: int = 200) -> list[dict]:
        """Newest first; one person's (user_id) or everyone's (None)."""
        query = select(access_log).order_by(access_log.c.id.desc()).limit(limit)
        if user_id is not None:
            query = query.where(access_log.c.user_id == user_id)
        with self.engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        out = []
        for r in rows:
            try:
                detail = self.cipher.open_text(r["detail"])
            except DecryptError:
                detail = None
            at = r["at"] if r["at"].tzinfo else r["at"].replace(tzinfo=timezone.utc)
            out.append({"id": r["id"], "at": at.isoformat(timespec="seconds"), "user_id": r["user_id"],
                        "user_name": r["user_name"], "via": r["via"], "action": r["action"],
                        "what": ACTIONS.get(r["action"], r["action"]), "document_id": r["document_id"],
                        "detail": detail, "client": r["client"], "ok": r["ok"]})
        return out
