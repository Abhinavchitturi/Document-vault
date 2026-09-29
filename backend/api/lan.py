"""The vault on the home network, for the phone app (Phase 7): HTTPS with a certificate the
app pins.

    python -m backend.api.app --phones      # also serves https://<this Mac's address>:8443

The Mac's own browser keeps using http://localhost:8000. The phone listener:
    - speaks only HTTPS, with a certificate made on this computer (data/tls/, 10 years). No
      certificate authority is involved: the pairing QR code carries the certificate's
      SHA-256 fingerprint and the app refuses any server that doesn't match it exactly.
    - serves only the API, only to paired phones (Authorization: Bearer <device key>), plus the
      pairing call itself. No web pages, no browser cookies.

This file makes the certificate and finds the Mac's address on the network.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import socket
from pathlib import Path

PHONE_PORT = 8443


def lan_address() -> str:
    """This computer's address on the home network (the one phones on the same Wi-Fi can reach)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9))  # picks the outgoing interface; no packet is sent for UDP connect
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def ensure_certificate(folder: Path, address: str) -> tuple[Path, Path, str]:
    """(cert file, key file, SHA-256 fingerprint of the certificate). Made once, then reused;
    remade if the network address changed (the app re-pairs by scanning a new QR code)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "vault-cert.pem", folder / "vault-key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            ips = {str(ip) for ip in names.get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            ips = set()
        if address in ips and cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
            return cert_path, key_path, fingerprint(cert_path)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Document Vault")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                    x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
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
