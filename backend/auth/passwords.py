"""Password hashing with scrypt (Python's standard library: no extra package).

scrypt is deliberately slow and memory-hungry (about 0.1 s and 32 MB per check), so a
stolen database can't be tested against millions of guesses per second. Each password
gets its own random salt. Stored as: scrypt$<n>$<r>$<p>$<salt>$<hash> (base64).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**15, 8, 1
MAXMEM = 64 * 1024 * 1024
MIN_LENGTH, MAX_LENGTH = 8, 200


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"The password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"The password must be at most {MAX_LENGTH} characters.")
    if password.strip() != password or not password.strip():
        raise ValueError("The password can't start or end with a space.")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, N, R, P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        _derive(password, b"\0" * 16, N, R, P)  # same time whether or not the account exists
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))
