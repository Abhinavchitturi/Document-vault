"""Family accounts and sign-in sessions (Phase 6).

    First run      nobody has a password yet: the setup screen lets the vault's owner choose
                   one and makes them the admin. Their existing documents stay theirs.
    Admin          adds family members and resets forgotten passwords (Settings).
    Everyone       signs in with name + password and sees only their own documents; can
                   change their own password.

A sign-in creates a session: a random token in an HttpOnly cookie. The database keeps only
the token's SHA-256, with an expiry (SESSION_DAYS). Changing or resetting a password signs
that person out everywhere else.

Guessing is slowed twice: scrypt makes each check cost ~0.1 s, and after MAX_FAILURES wrong
passwords for a name (or from one address) within LOCKOUT, sign-in waits for LOCKOUT.

Locked out of every account? From a terminal on this computer:
    python -m backend.auth.cli reset-password <name>
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select

from backend.auth.passwords import check_strength, hash_password, verify_password
from backend.database.schema import sessions, users, vault_meta

SESSION_DAYS = 14
MAX_FAILURES = 5
LOCKOUT = 15 * 60  # seconds


class AuthError(Exception):
    """Something the person can fix; the message is safe to show them."""


class TooManyAttempts(AuthError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # SQLite drops the zone


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not 1 <= len(name) <= 60:
        raise AuthError("The name must be 1 to 60 characters.")
    return name


def _ensure_an_admin(conn, user_id: int) -> None:
    """If nobody who can sign in is an admin (e.g. the first password was set from the terminal
    instead of the setup screen), this person becomes one, so someone can manage the family."""
    admins = conn.execute(select(func.count()).where(users.c.is_admin.is_(True), users.c.password_hash.is_not(None)))
    if not admins.scalar():
        conn.execute(users.update().where(users.c.id == user_id).values(is_admin=True))


def _public(row) -> dict:
    return {"id": row.id, "name": row.name, "is_admin": bool(row.is_admin)}


class Accounts:
    def __init__(self, engine):
        self.engine = engine
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # ---------- first run ----------

    def setup_needed(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).where(users.c.password_hash.is_not(None))).scalar() == 0

    def suggested_name(self) -> str | None:
        """The vault's current owner when there is exactly one, to pre-fill the setup screen."""
        with self.engine.connect() as conn:
            names = conn.execute(select(users.c.name).where(users.c.password_hash.is_(None)).limit(2)).scalars().all()
        return names[0] if len(names) == 1 else None

    def setup(self, name: str, password: str) -> dict:
        """Create the first account (the admin). Adopts the existing owner so their documents stay theirs."""
        name = clean_name(name)
        self._check_password(password)
        with self._lock, self.engine.begin() as conn:
            if conn.execute(select(func.count()).where(users.c.password_hash.is_not(None))).scalar():
                raise AuthError("The vault is already set up. Sign in instead.")
            row = conn.execute(select(users).where(users.c.name == name)).first()
            if row is None:
                unclaimed = conn.execute(select(users).where(users.c.password_hash.is_(None)).limit(2)).all()
                if len(unclaimed) == 1:  # the single existing owner, renamed to what they typed
                    row = unclaimed[0]
                    conn.execute(users.update().where(users.c.id == row.id).values(name=name))
            if row is None:
                user_id = conn.execute(insert(users).values(name=name, created_at=_now()).returning(users.c.id)).scalar_one()
            else:
                user_id = row.id
            conn.execute(users.update().where(users.c.id == user_id)
                         .values(password_hash=hash_password(password), is_admin=True))
            conn.execute(delete(vault_meta).where(vault_meta.c.key == "owner_named_by"))
            conn.execute(insert(vault_meta).values(key="owner_named_by", value="person"))
            return _public(conn.execute(select(users).where(users.c.id == user_id)).one())

    # ---------- signing in and out ----------

    def throttle(self, keys: list[str]) -> None:
        cutoff = time.monotonic() - LOCKOUT
        with self._lock:
            for key in keys:
                recent = [t for t in self._failures.get(key, []) if t > cutoff]
                self._failures[key] = recent
                if len(recent) >= MAX_FAILURES:
                    minutes = max(1, round((recent[0] + LOCKOUT - time.monotonic()) / 60))
                    raise TooManyAttempts(f"Too many wrong passwords. Try again in {minutes} minutes.")

    def record_failure(self, keys: list[str]) -> None:
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, []).append(time.monotonic())

    def _find(self, conn, name: str):
        """Exact name, or a case-insensitive match when that is unambiguous ("abhinav" = "Abhinav")."""
        row = conn.execute(select(users).where(users.c.name == name)).first()
        if row is None:
            matches = conn.execute(select(users).where(func.lower(users.c.name) == name.lower()).limit(2)).all()
            row = matches[0] if len(matches) == 1 else None
        return row

    def login(self, name: str, password: str, client: str = "") -> tuple[dict, str]:
        """(user, session token). Raises AuthError with a message that doesn't reveal which part was wrong."""
        name = " ".join((name or "").split())
        keys = [f"name:{name.lower()}", f"client:{client}"]
        self.throttle(keys)
        with self.engine.connect() as conn:
            row = self._find(conn, name) if name else None
        if not verify_password(password or "", row.password_hash if row else None):
            self.record_failure(keys)
            raise AuthError("Wrong name or password.")
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
        return _public(row), self.start_session(row.id)

    def start_session(self, user_id: int) -> str:
        """A new signed-in session (after a password or passkey check); returns the cookie token."""
        token = secrets.token_urlsafe(32)
        now = _now()
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.expires_at < now))  # tidy up old ones
            conn.execute(insert(sessions).values(token_hash=_token_hash(token), user_id=user_id, created_at=now,
                                                 expires_at=now + timedelta(days=SESSION_DAYS)))
        return token

    def user_for_token(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users, sessions.c.expires_at).join(sessions, sessions.c.user_id == users.c.id)
                .where(sessions.c.token_hash == _token_hash(token))
            ).first()
        if row is None or _utc(row.expires_at) < _now() or not row.password_hash:
            return None
        return _public(row)

    def logout(self, token: str | None) -> None:
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(sessions).where(sessions.c.token_hash == _token_hash(token)))

    # ---------- passwords and members ----------

    @staticmethod
    def _check_password(password: str) -> None:
        try:
            check_strength(password or "")
        except ValueError as e:
            raise AuthError(str(e)) from e

    def change_password(self, user_id: int, current: str, new: str, keep_token: str | None = None) -> None:
        self.throttle([f"user:{user_id}"])
        with self.engine.connect() as conn:
            stored = conn.execute(select(users.c.password_hash).where(users.c.id == user_id)).scalar()
        if not verify_password(current or "", stored):
            self.record_failure([f"user:{user_id}"])
            raise AuthError("Your current password is wrong.")
        self.set_password(user_id, new, keep_token=keep_token)

    def set_password(self, user_id: int, new: str, keep_token: str | None = None) -> None:
        """Set a password and sign that person out everywhere (except the session `keep_token`)."""
        self._check_password(new)
        with self.engine.begin() as conn:
            if conn.execute(users.update().where(users.c.id == user_id)
                            .values(password_hash=hash_password(new))).rowcount != 1:
                raise AuthError("No such family member.")
            _ensure_an_admin(conn, user_id)
            ended = delete(sessions).where(sessions.c.user_id == user_id)
            if keep_token:
                ended = ended.where(sessions.c.token_hash != _token_hash(keep_token))
            conn.execute(ended)

    def add_member(self, name: str, password: str, is_admin: bool = False) -> dict:
        name = clean_name(name)
        self._check_password(password)
        with self.engine.begin() as conn:
            row = conn.execute(select(users).where(func.lower(users.c.name) == name.lower())).first()
            if row is not None and row.password_hash:
                raise AuthError(f"{row.name} already has an account.")
            values = {"password_hash": hash_password(password), "is_admin": is_admin}
            if row is None:
                user_id = conn.execute(insert(users).values(name=name, created_at=_now(), **values)
                                       .returning(users.c.id)).scalar_one()
            else:  # someone who owned documents before Phase 6 (--owner): give them a sign-in
                user_id = row.id
                conn.execute(users.update().where(users.c.id == user_id).values(**values))
            _ensure_an_admin(conn, user_id)
            return _public(conn.execute(select(users).where(users.c.id == user_id)).one())

    def find_user(self, name: str) -> dict | None:
        with self.engine.connect() as conn:
            row = self._find(conn, " ".join(name.split()))
        return _public(row) if row else None

    def list_users(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(users).order_by(users.c.created_at, users.c.id)).all()
        return [{**_public(r), "can_sign_in": bool(r.password_hash)} for r in rows]
