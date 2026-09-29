"""A software passkey device for tests: makes real, signed WebAuthn responses (P-256, "none"
attestation), like a Mac's Touch ID would, so the server's verification runs for real."""

from __future__ import annotations

import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

UP, UV, AT = 0x01, 0x04, 0x40


class SoftDevice:
    def __init__(self, origin="http://testserver", user_verifies=True):
        self.origin = origin
        self.user_verifies = user_verifies
        self.credentials: dict[bytes, dict] = {}  # credential id -> key, rp_id, user handle, counter

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def _flags(self, extra: int = 0) -> int:
        return UP | (UV if self.user_verifies else 0) | extra

    def create(self, options: dict | str, origin: str | None = None) -> dict:
        """navigator.credentials.create() -> JSON as the browser would send it."""
        options = json.loads(options) if isinstance(options, str) else options
        rp_id = options["rp"]["id"]
        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        cred_id = os.urandom(16)
        auth_data = (hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags(AT)]) + struct.pack(">I", 0)
                     + b"\0" * 16 + struct.pack(">H", len(cred_id)) + cred_id + cose)
        self.credentials[cred_id] = {"key": key, "rp_id": rp_id, "user": options["user"]["id"], "count": 0}
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cid = bytes_to_base64url(cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(client_data),
                             "attestationObject": bytes_to_base64url(attestation), "transports": ["internal"]}}

    def get(self, options: dict | str, cred_id: bytes | None = None, origin: str | None = None) -> dict:
        """navigator.credentials.get() with this device's (only, or chosen) passkey for the site."""
        options = json.loads(options) if isinstance(options, str) else options
        cred_id = cred_id or next(c for c, v in self.credentials.items() if v["rp_id"] == options["rpId"])
        cred = self.credentials[cred_id]
        cred["count"] += 1
        auth_data = hashlib.sha256(options["rpId"].encode()).digest() + bytes([self._flags()]) + struct.pack(">I", cred["count"])
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = cred["key"].sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        cid = bytes_to_base64url(cred_id)
        return {"id": cid, "rawId": cid, "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(client_data),
                             "authenticatorData": bytes_to_base64url(auth_data),
                             "signature": bytes_to_base64url(signature), "userHandle": cred["user"]}}


def handle_of(credential_user_b64: str) -> bytes:
    return base64url_to_bytes(credential_user_b64)
