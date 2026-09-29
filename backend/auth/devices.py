"""Pairing phones with the vault (Phase 7).

    Mac:   Settings -> Phones -> Pair a phone   shows a QR code (valid 10 minutes, one use)
    Phone: the app scans it, pins the vault's certificate, and swaps the one-time code for its
           own access key, kept in the phone's Keychain / Keystore.

The QR code holds the vault's address on the home network, the SHA-256 fingerprint of its
certificate, and the code. The phone then only talks to a server with exactly that
certificate, so nobody else on the Wi-Fi can pose as the vault or read the traffic.

Each phone's key is random, stored here only as a SHA-256, sent as `Authorization: Bearer`,
and can be revoked in Settings (or ends when the admin resets that person's password).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select

from backend.auth.accounts import AuthError, _public
from backend.database.schema import devices, users

PAIRING_SECONDS = 10 * 60
SEEN_EVERY = timedelta(minutes=5)  # how often last_seen_at is written (not on every request)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    def __init__(self, engine):
        self.engine = engine
        self._codes: dict[str, tuple[int, float]] = {}  # pairing code -> (user id, expires)
        self._lock = threading.Lock()

    def start_pairing(self, user_id: int) -> tuple[str, int]:
        """A one-time pairing code for this person, and how many seconds it lasts."""
        code = secrets.token_urlsafe(12)
        now = time.monotonic()
        with self._lock:
            self._codes = {c: v for c, v in self._codes.items() if v[1] > now and v[0] != user_id}  # one open code each
            self._codes[code] = (user_id, now + PAIRING_SECONDS)
        return code, PAIRING_SECONDS

    def pair(self, code: str, name: str) -> tuple[dict, dict, str]:
        """(user, device, access key) for a valid code; the code is used up either way."""
        with self._lock:
            entry = self._codes.pop(code or "", None)
        if entry is None or entry[1] < time.monotonic():
            raise AuthError("This pairing code is wrong or expired. Show a new QR code on the Mac.")
        name = " ".join((name or "").split())[:100] or "Phone"
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            device_id = conn.execute(insert(devices).values(
                user_id=entry[0], name=name, token_hash=_hash(token), created_at=_now(), last_seen_at=_now(),
            ).returning(devices.c.id)).scalar_one()
            user = conn.execute(select(users).where(users.c.id == entry[0])).one()
        return _public(user), {"id": device_id, "name": name}, token

    def user_for_token(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users, devices.c.id.label("device_id"), devices.c.name.label("device_name"), devices.c.last_seen_at)
                .join(devices, devices.c.user_id == users.c.id).where(devices.c.token_hash == _hash(token))
            ).first()
        if row is None or not row.password_hash:
            return None
        seen = row.last_seen_at.replace(tzinfo=row.last_seen_at.tzinfo or timezone.utc) if row.last_seen_at else None
        if seen is None or _now() - seen > SEEN_EVERY:
            with self.engine.begin() as conn:
                conn.execute(devices.update().where(devices.c.id == row.device_id).values(last_seen_at=_now()))
        return {**_public(row), "device_id": row.device_id, "device_name": row.device_name}

    def list_for(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(devices).where(devices.c.user_id == user_id).order_by(devices.c.created_at)).all()
        iso = lambda d: d.replace(tzinfo=d.tzinfo or timezone.utc).isoformat(timespec="seconds") if d else None  # noqa: E731
        return [{"id": r.id, "name": r.name, "created_at": iso(r.created_at), "last_seen_at": iso(r.last_seen_at)}
                for r in rows]

    def revoke(self, user_id: int, device_id: int) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(delete(devices).where(devices.c.id == device_id, devices.c.user_id == user_id)).rowcount == 1

    def revoke_all(self, user_id: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(devices).where(devices.c.user_id == user_id)).rowcount
