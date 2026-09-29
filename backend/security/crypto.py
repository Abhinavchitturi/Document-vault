"""Encryption at rest (Phase 11): AES-256-GCM for everything the vault writes about your documents.

    files/<doc_id>.pdf, .jpg ...   sealed bytes (magic "DVE1" + 12-byte nonce + ciphertext and tag)
    files/<doc_id>.ocr.json        sealed the same way
    vectors.faiss                  sealed the same way
    database text                  "dv1:" + base64(nonce + ciphertext): file names, passage text,
                                   policy / PAN / Aadhaar numbers, issuers

GCM authenticates as well as hides: a changed or truncated file fails to open instead of
returning garbage. Each kind of data has its own associated data ("file", "text", ...) so a
sealed value cannot be moved into a different slot and still open.

Values written before Phase 11 (no magic / prefix) are passed through as they are, which lets
an existing vault be opened and encrypted in place (see DocumentStore._encrypt_existing).
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"DVE1"
TEXT_PREFIX = "dv1:"
KEY_BYTES = 32
NONCE_BYTES = 12


class DecryptError(Exception):
    """Wrong key, or the data was changed or damaged."""


class Cipher:
    def __init__(self, key: bytes):
        if len(key) != KEY_BYTES:
            raise ValueError("the vault key must be 32 bytes")
        self._aead = AESGCM(key)

    # ---------- bytes (files, OCR, vector index) ----------

    def seal(self, data: bytes, kind: str = "file") -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return MAGIC + nonce + self._aead.encrypt(nonce, data, kind.encode())

    def open(self, data: bytes, kind: str = "file") -> bytes:
        if not is_sealed(data):
            return data  # written before encryption was switched on
        nonce = data[len(MAGIC):len(MAGIC) + NONCE_BYTES]
        try:
            return self._aead.decrypt(nonce, data[len(MAGIC) + NONCE_BYTES:], kind.encode())
        except InvalidTag as e:
            raise DecryptError("could not decrypt: wrong vault key, or the data was changed") from e

    # ---------- database text ----------

    def seal_text(self, value: str | None) -> str | None:
        if value is None or is_sealed_text(value):
            return value
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, value.encode("utf-8"), b"text")
        return TEXT_PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def open_text(self, value: str | None) -> str | None:
        if value is None or not is_sealed_text(value):
            return value
        raw = base64.b64decode(value[len(TEXT_PREFIX):])
        try:
            return self._aead.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], b"text").decode("utf-8")
        except InvalidTag as e:
            raise DecryptError("could not decrypt: wrong vault key, or the data was changed") from e


def is_sealed(data: bytes) -> bool:
    return data[:len(MAGIC)] == MAGIC


def is_sealed_text(value: str) -> bool:
    return value.startswith(TEXT_PREFIX)


def new_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


# ---------- recovery key: the vault key written out for a person to keep ----------

def recovery_code(key: bytes) -> str:
    """The key as 13 groups of 4 letters/digits (base32, no padding), e.g. 'K7QX-2M4P-...'."""
    text = base64.b32encode(key).decode("ascii").rstrip("=")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def key_from_recovery_code(code: str) -> bytes:
    text = "".join(ch for ch in code.upper() if ch.isalnum())
    text = text.replace("0", "O").replace("1", "I").replace("8", "B")  # common misreadings; base32 has none of these
    try:
        key = base64.b32decode(text + "=" * (-len(text) % 8))
    except ValueError as e:
        raise ValueError("that recovery key has a typo: it should be 13 groups of letters and digits 2-7") from e
    if len(key) != KEY_BYTES:
        raise ValueError("that recovery key is the wrong length: it should be 13 groups of 4")
    return key
