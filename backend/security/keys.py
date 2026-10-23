"""Where the vault key lives: the macOS Keychain, never inside the vault folder.

Keeping the key apart from the data is the point: a copy of data/ (a backup disk, a synced
folder, a stolen laptop's drive image) holds only ciphertext. On the Mac the key sits in the
login Keychain under "Document Vault" (Keychain Access app → search "Document Vault").

Where no Keychain is available (Linux, CI, VAULT_KEYCHAIN=off) the key goes in a file under
~/.document-vault/keys/ (or VAULT_KEY_DIR), readable only by you, still outside the vault folder.

Each vault has an id (vault_meta "vault_id"), so two vaults on one Mac have two keys.
If the key is lost, the recovery key (python -m backend.security.cli recovery-key) brings it back.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE = "Document Vault"


class KeyMissingError(Exception):
    """The vault is encrypted but this Mac does not have its key."""


def _keyring():
    if os.environ.get("VAULT_KEYCHAIN", "").lower() in ("off", "0", "no", "false"):
        return None
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    backend = keyring.get_keyring()
    if isinstance(backend, fail.Keyring):
        return None
    return keyring


def _key_dir() -> Path:
    return Path(os.environ.get("VAULT_KEY_DIR") or Path.home() / ".document-vault" / "keys")


def where(vault_id: str) -> str:
    """Said to the person: where this vault's key is kept."""
    return "the macOS Keychain (\"Document Vault\")" if _keyring() else str(_key_dir() / f"{vault_id}.key")


def load(vault_id: str) -> bytes | None:
    ring = _keyring()
    if ring is not None:
        try:
            value = ring.get_password(SERVICE, vault_id)
        except Exception as e:  # a locked or refused Keychain
            logger.warning("Keychain did not answer (%s); looking for a key file instead", e)
            value = None
        if value:
            return base64.b64decode(value)
    path = _key_dir() / f"{vault_id}.key"
    if path.exists():
        return base64.b64decode(path.read_text().strip())
    return None


def save(vault_id: str, key: bytes) -> str:
    """Store the key; returns where it went."""
    value = base64.b64encode(key).decode("ascii")
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, vault_id, value)
            return where(vault_id)
        except Exception as e:
            logger.warning("could not save to the Keychain (%s); using a key file", e)
    folder = _key_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / f"{vault_id}.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value + "\n")
    return str(path)


# ---------- the backup password for scheduled backups ----------
# Scheduled backups run with nobody at the keyboard, so the backup password has to be kept: in the
# same place as the vault key (Keychain, else a 0600 file outside the vault folder). A backup is
# still ciphertext on the NAS or disk; this only lets this Mac write new ones.

def _backup_account(vault_id: str) -> str:
    return f"{vault_id}:backup-password"


def load_backup_password(vault_id: str) -> str | None:
    ring = _keyring()
    if ring is not None:
        try:
            value = ring.get_password(SERVICE, _backup_account(vault_id))
        except Exception as e:
            logger.warning("Keychain did not answer (%s); looking for a file instead", e)
            value = None
        if value:
            return value
    path = _key_dir() / f"{vault_id}.backup-password"
    return path.read_text().strip() if path.exists() else None


def save_backup_password(vault_id: str, password: str) -> None:
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(SERVICE, _backup_account(vault_id), password)
            return
        except Exception as e:
            logger.warning("could not save to the Keychain (%s); using a file", e)
    folder = _key_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    fd = os.open(folder / f"{vault_id}.backup-password", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password + "\n")


def delete_backup_password(vault_id: str) -> None:
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(SERVICE, _backup_account(vault_id))
        except Exception:  # nothing stored there
            pass
    (_key_dir() / f"{vault_id}.backup-password").unlink(missing_ok=True)
