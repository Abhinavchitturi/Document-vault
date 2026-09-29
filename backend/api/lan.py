"""The vault for the phone app (Phase 7): HTTPS with a certificate the app pins, reachable on
the home Wi-Fi and, through a private tunnel, from anywhere.

    python -m backend.api.app --phones      # also serves https://<address>:8443 to paired phones

The Mac's own browser keeps using http://localhost:8000. The phone listener:
    - speaks only HTTPS, with a certificate made on this computer (data/tls/, 10 years). No
      certificate authority is involved: the pairing QR code carries the certificate's
      SHA-256 fingerprint and the app refuses any server that doesn't match it exactly. The
      certificate doesn't depend on the address, so it stays the same on any network.
    - serves only the API, only to paired phones (Authorization: Bearer <device key>), plus the
      pairing call itself. No web pages, no browser cookies.

Addresses given to phones (the app tries them in order and keeps the one that answers):
    - this Mac's address on the current Wi-Fi (home router, or a phone's hotspot)
    - its Tailscale address, if Tailscale is installed: a private tunnel between your own devices,
      so a phone in another state reaches the Mac at home over mobile data. Nothing is opened to
      the public internet; the Mac must be on, awake and online.
    - any extra addresses given with --phone-address
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path

PHONE_PORT = 8443
TAILSCALE_CLI = ("tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
TAILNET = ipaddress.ip_network("100.64.0.0/10")  # the range Tailscale gives devices


def lan_address() -> str | None:
    """This computer's address on the local network (the one phones on the same Wi-Fi can reach)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))  # picks the outgoing interface; no packet is sent for UDP connect
        address = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    ip = ipaddress.ip_address(address)
    return None if ip.is_loopback or ip in TAILNET else address


def tailscale_address() -> str | None:
    """This Mac's Tailscale IPv4 address, if Tailscale is installed and connected."""
    for cli in TAILSCALE_CLI:
        path = shutil.which(cli) or (cli if Path(cli).exists() else None)
        if not path:
            continue
        try:
            out = subprocess.run([path, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.split():
            try:
                if ipaddress.ip_address(line) in TAILNET:
                    return line
            except ValueError:
                continue
    return None


def phone_urls(port: int, extra: list[str] | None = None) -> list[str]:
    """Every address a phone might reach this Mac on, most local first."""
    found = [lan_address(), tailscale_address(), *(extra or [])]
    urls = []
    for address in found:
        if address:
            host = f"[{address}]" if ":" in address and not address.startswith("[") else address
            url = f"https://{host}:{port}"
            if url not in urls:
                urls.append(url)
    return urls


def ensure_certificate(folder: Path) -> tuple[Path, Path, str]:
    """(cert file, key file, SHA-256 fingerprint of the certificate). Made once and reused on
    every network; remade only when it nears expiry (then phones pair again)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "vault-cert.pem", folder / "vault-key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
            return cert_path, key_path, fingerprint(cert_path)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Document Vault")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("document-vault.local")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, fingerprint(cert_path)


def fingerprint(cert_path: Path) -> str:
    """SHA-256 of the certificate (DER), as lowercase hex: what the app compares against."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def keep_awake() -> None:
    """On a Mac, stop it from idle-sleeping while the vault serves phones (ends with the vault)."""
    import os
    import sys

    if sys.platform == "darwin" and shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
