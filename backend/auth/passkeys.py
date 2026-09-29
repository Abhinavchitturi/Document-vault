"""Passkeys: sign in with Touch ID, Face ID, or a phone's fingerprint (WebAuthn).

A passkey is a key pair made by the device. The private key stays in the device's secure
chip (Mac Secure Enclave, iPhone, Android) and is only used after your fingerprint or face;
the vault stores just the public key. Signing in, the vault sends a random challenge, the
device signs it, and the vault checks the signature. Nothing secret crosses the network or
sits in the database, and a passkey can't be phished: the browser only uses it on the site
that created it.

    Settings -> You -> Add a passkey      (signed in; the device asks for Touch ID)
    Sign-in screen -> Sign in with a passkey

Passkeys belong to a web address, and browsers refuse IP addresses, so the vault must be
opened as http://localhost:8000 (not 127.0.0.1). `allowed_hosts` lists the names passkeys
may be used on; Phase 13 adds the vault's HTTPS name for phones.

Each ceremony's challenge is kept here for a few minutes and can be used once.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from backend.auth.accounts import AuthError, _public
from backend.database.schema import passkeys, users

RP_NAME = "Document Vault"
CEREMONY_SECONDS = 5 * 60
MAX_PASSKEYS = 20


@dataclass
class _Ceremony:
    challenge: bytes
    purpose: str  # "register" or "login"
    user_id: int | None
    rp_id: str
    origin: str
    expires: float


def _now() -> datetime:
    return datetime.now(timezone.utc)


def user_handle(user_id: int) -> bytes:
    """What the device stores as "whose passkey this is" (not personal: just the account number)."""
    return f"vault-user:{user_id}".encode()


class Passkeys:
    def __init__(self, engine, allowed_hosts: tuple[str, ...] = ("localhost",)):
        self.engine = engine
        self.allowed_hosts = allowed_hosts
        self._ceremonies: dict[str, _Ceremony] = {}
        self._lock = threading.Lock()

    # ---------- where passkeys can be used ----------

    def site(self, scheme: str, host_header: str) -> tuple[str, str]:
        """(rp_id, origin) for a request, or AuthError if passkeys can't work on this address."""
        hostname = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header
        if hostname not in self.allowed_hosts:
            port = host_header.rsplit(":", 1)[1] if ":" in host_header and not host_header.startswith("[") else ""
            better = f"http://localhost{':' + port if port else ''}"
            raise AuthError(f"Passkeys only work when the vault is opened as {better} (browsers don't allow them on "
                            f"{hostname}).")
        return hostname, f"{scheme}://{host_header}"

    def _start(self, purpose: str, challenge: bytes, user_id: int | None, rp_id: str, origin: str) -> str:
        ceremony_id = secrets.token_urlsafe(16)
        now = time.monotonic()
        with self._lock:
            self._ceremonies = {k: c for k, c in self._ceremonies.items() if c.expires > now}  # forget stale ones
            self._ceremonies[ceremony_id] = _Ceremony(challenge, purpose, user_id, rp_id, origin, now + CEREMONY_SECONDS)
        return ceremony_id

    def _finish(self, ceremony_id: str, purpose: str) -> _Ceremony:
        with self._lock:
            ceremony = self._ceremonies.pop(ceremony_id or "", None)  # one use only
        if ceremony is None or ceremony.purpose != purpose or ceremony.expires < time.monotonic():
            raise AuthError("That took too long or was already used. Please try again.")
        return ceremony

    # ---------- adding a passkey (signed in) ----------

    def registration_options(self, user: dict, rp_id: str, origin: str) -> dict:
        with self.engine.connect() as conn:
            existing = conn.execute(select(passkeys.c.id).where(passkeys.c.user_id == user["id"])).scalars().all()
        if len(existing) >= MAX_PASSKEYS:
            raise AuthError(f"You already have {MAX_PASSKEYS} passkeys. Remove one first.")
        options = generate_registration_options(
            rp_id=rp_id, rp_name=RP_NAME, user_name=user["name"], user_display_name=user["name"],
            user_id=user_handle(user["id"]),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,  # sign in without typing your name
                user_verification=UserVerificationRequirement.REQUIRED,  # fingerprint / face / device PIN
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in existing],
        )
        ceremony = self._start("register", options.challenge, user["id"], rp_id, origin)
        return {"ceremony": ceremony, "options": options_to_json(options)}

    def register(self, user: dict, ceremony_id: str, credential: dict, name: str) -> dict:
        ceremony = self._finish(ceremony_id, "register")
        if ceremony.user_id != user["id"]:
            raise AuthError("That passkey request was for someone else.")
        try:
            verified = verify_registration_response(
                credential=credential, expected_challenge=ceremony.challenge, expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin, require_user_verification=True,
            )
        except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as e:
            raise AuthError("The device's answer couldn't be verified. Please try again.") from e
        credential_id = bytes_to_base64url(verified.credential_id)
        name = " ".join((name or "").split())[:100] or "Passkey"
        with self.engine.begin() as conn:
            if conn.execute(select(passkeys.c.id).where(passkeys.c.id == credential_id)).first():
                raise AuthError("This passkey is already added.")
            conn.execute(insert(passkeys).values(
                id=credential_id, user_id=user["id"], public_key=verified.credential_public_key,
                sign_count=verified.sign_count, name=name, created_at=_now(),
            ))
        return {"id": credential_id, "name": name}

    # ---------- signing in with one ----------

    def login_options(self, rp_id: str, origin: str) -> dict:
        # No list of allowed passkeys: the device offers the ones it has for this site, so nobody
        # has to type a name, and the page doesn't reveal who has accounts.
        options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
        return {"ceremony": self._start("login", options.challenge, None, rp_id, origin),
                "options": options_to_json(options)}

    def login(self, ceremony_id: str, credential: dict) -> dict:
        """The signed-in user, or AuthError."""
        ceremony = self._finish(ceremony_id, "login")
        try:
            credential_id = parse_authentication_credential_json(credential).id
        except Exception as e:  # malformed JSON from the browser
            raise AuthError("The device's answer couldn't be read. Please try again.") from e
        with self.engine.connect() as conn:
            row = conn.execute(
                select(passkeys, users.c.password_hash).join(users, users.c.id == passkeys.c.user_id)
                .where(passkeys.c.id == credential_id)
            ).first()
        if row is None:
            raise AuthError("This passkey isn't registered with the vault (it may have been removed). "
                            "Sign in with your password.")
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=ceremony.challenge, expected_rp_id=ceremony.rp_id,
                expected_origin=ceremony.origin, credential_public_key=row.public_key,
                credential_current_sign_count=row.sign_count, require_user_verification=True,
            )
        except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError) as e:
            raise AuthError("The passkey couldn't be verified. Please try again.") from e
        handle = (credential.get("response") or {}).get("userHandle")
        if handle and base64url_to_bytes(handle) != user_handle(row.user_id):
            raise AuthError("The passkey couldn't be verified. Please try again.")
        with self.engine.begin() as conn:
            conn.execute(passkeys.update().where(passkeys.c.id == credential_id)
                         .values(sign_count=verified.new_sign_count, last_used_at=_now()))
            user = conn.execute(select(users).where(users.c.id == row.user_id)).one()
        return _public(user)

    # ---------- managing ----------

    def list_for(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(passkeys).where(passkeys.c.user_id == user_id).order_by(passkeys.c.created_at)).all()
        iso = lambda d: d.replace(tzinfo=d.tzinfo or timezone.utc).isoformat(timespec="seconds") if d else None  # noqa: E731
        return [{"id": r.id, "name": r.name, "created_at": iso(r.created_at), "last_used_at": iso(r.last_used_at)}
                for r in rows]

    def remove(self, user_id: int, passkey_id: str) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(delete(passkeys).where(passkeys.c.id == passkey_id, passkeys.c.user_id == user_id)).rowcount == 1

    def remove_all(self, user_id: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(passkeys).where(passkeys.c.user_id == user_id)).rowcount
