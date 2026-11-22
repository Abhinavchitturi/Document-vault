"""Calling another computer that keeps the vault: HTTPS to its device port, trusting only the certificate it was
paired with (its SHA-256, checked before anything is sent), with the key it gave this computer."""
from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from urllib.parse import urlsplit


class Unreachable(Exception):
    """That computer couldn't be reached at this address (off, another network), or isn't the one paired with."""


def fingerprint_of(url: str, timeout: float = 8) -> str:
    """The SHA-256 of the certificate the computer at `url` shows (to show its security code before pairing)."""
    connection = _connect(url, timeout)
    try:
        return hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
    finally:
        connection.close()


def _connect(url: str, timeout: float) -> http.client.HTTPSConnection:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # a home computer's own certificate: trusted by its fingerprint instead
    connection = http.client.HTTPSConnection(parts.hostname, parts.port or 8443, timeout=timeout, context=context)
    try:
        connection.connect()
    except (OSError, ssl.SSLError) as e:
        raise Unreachable(f"{parts.hostname}: {e}") from e
    return connection


def call(url: str, fingerprint: str, method: str, path: str, token: str | None = None, body: dict | None = None,
         timeout: float = 60) -> tuple[int, bytes, dict]:
    """(status, body, headers) from the computer at `url`, if its certificate is the paired one."""
    connection = _connect(url, timeout)
    try:
        seen = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if seen != fingerprint:
            raise Unreachable(f"{url} isn't the computer this one was paired with (another certificate)")
        headers = {"Accept": "application/json"}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
    except (OSError, http.client.HTTPException) as e:
        raise Unreachable(f"{url}: {e}") from e
    finally:
        connection.close()


def call_json(url: str, fingerprint: str, method: str, path: str, token: str | None = None, body: dict | None = None,
              timeout: float = 60):
    status, data, _ = call(url, fingerprint, method, path, token, body, timeout)
    if status >= 400:
        try:
            detail = json.loads(data).get("detail")
        except ValueError:
            detail = None
        raise PermissionError(detail or f"the other computer answered {status}") if status in (401, 403) \
            else RuntimeError(detail or f"the other computer answered {status}")
    return json.loads(data) if data else None
