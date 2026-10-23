"""Encrypted backups (Phase 11): the whole vault in one file that only your backup password opens.

    python -m backend.security.cli backup ~/Desktop            # or Settings → Security on the Mac
    python -m backend.security.cli restore "<file>.dvbackup"   # on a new Mac, or after a disk failure

The file holds every table (people, passkeys, paired phones, documents, reminders, activity),
the encrypted document files and the vector index, and the vault key, wrapped with a key made
from the backup password (scrypt). So a backup restores on a Mac that has never seen the vault,
and it is safe to keep on a USB drive, iCloud Drive or Google Drive: without the password it is noise.

Layout (all integers big-endian):
    "DVBACKUP1"  header length (4 bytes)  header JSON
    then chunks: length (4) + nonce (12) + AES-256-GCM ciphertext of up to 1 MB of a zip file

Each chunk's associated data is the header, its number and whether it is the last one, so chunks
cannot be dropped, reordered or cut off without the restore noticing.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import Date, DateTime, LargeBinary, delete, func, insert, select, text

from backend.database.db import connect
from backend.database.schema import documents, metadata
from backend.security import keys
from backend.vector_store import store as store_module

MAGIC = b"DVBACKUP1"
CHUNK = 1 << 20
MIN_PASSWORD = 10
SKIPPED_TABLES = {"sessions"}  # sign-ins don't travel: everyone signs in again after a restore
SUFFIX = ".dvbackup"


class BackupError(Exception):
    """Wrong backup password, a damaged file, or a restore that would overwrite a vault."""


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def _aad(header: bytes, number: int, last: bool) -> bytes:
    return header + struct.pack(">QB", number, last)


class _EncryptingWriter:
    """A write-only file object: what zipfile writes comes out as sealed chunks."""

    def __init__(self, out, aead: AESGCM, header: bytes):
        self.out, self.aead, self.header = out, aead, header
        self.buffer = bytearray()
        self.number = 0

    def write(self, data) -> int:
        self.buffer += data
        while len(self.buffer) > CHUNK:  # keep at least one byte back for the final chunk
            self._emit(bytes(self.buffer[:CHUNK]), last=False)
            del self.buffer[:CHUNK]
        return len(data)

    def flush(self) -> None:
        pass

    def _emit(self, data: bytes, last: bool) -> None:
        nonce = os.urandom(12)
        sealed = self.aead.encrypt(nonce, data, _aad(self.header, self.number, last))
        self.out.write(struct.pack(">I", len(sealed)) + nonce + sealed)
        self.number += 1

    def finish(self) -> None:
        self._emit(bytes(self.buffer), last=True)
        self.buffer.clear()


def _json_value(value):
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def _db_value(column, value):
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, LargeBinary):
        return base64.b64decode(value)
    return value


def make_backup(store, folder: str | Path, password: str, *, n: int = 2 ** 17) -> Path:
    """Write <folder>/Document Vault backup <date>.dvbackup; returns its path."""
    if len(password) < MIN_PASSWORD:
        raise BackupError(f"Use a backup password of at least {MIN_PASSWORD} characters.")
    vault = store._vault
    key = keys.load(vault.vault_id)
    salt = os.urandom(16)
    backup_key = _derive(password, salt, n, 8, 1)
    nonce = os.urandom(12)
    now = datetime.now(timezone.utc)
    header = json.dumps({
        "format": 1, "created": now.isoformat(timespec="seconds"), "vault_id": vault.vault_id,
        "kdf": {"name": "scrypt", "n": n, "r": 8, "p": 1, "salt": base64.b64encode(salt).decode()},
        "key": base64.b64encode(nonce + AESGCM(backup_key).encrypt(nonce, key, b"vault key")).decode(),
    }, sort_keys=True).encode()
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"Document Vault backup {now:%Y-%m-%d %H.%M}{SUFFIX}"
    partial = target.with_name(target.name + ".partial")
    with vault.lock, open(partial, "wb") as out:
        out.write(MAGIC + struct.pack(">I", len(header)) + header)
        writer = _EncryptingWriter(out, AESGCM(backup_key), header)
        with zipfile.ZipFile(writer, "w") as z:
            tables = {}
            with vault.engine.connect() as conn:
                for table in metadata.sorted_tables:
                    if table.name in SKIPPED_TABLES:
                        continue
                    rows = conn.execute(select(table)).mappings().all()
                    tables[table.name] = [{k: _json_value(v) for k, v in r.items()} for r in rows]
            z.writestr("database.json", json.dumps(tables), compress_type=zipfile.ZIP_DEFLATED)
            for path in sorted(vault.files_dir.iterdir()):
                if path.is_file() and not path.name.endswith(".tmp"):
                    z.write(path, f"files/{path.name}")  # already encrypted with the vault key
            index = vault.root / "vectors.faiss"
            if index.exists():
                z.write(index, "vectors.faiss")
        writer.finish()
    os.replace(partial, target)
    return target


def read_header(path: str | Path) -> dict:
    with open(path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise BackupError("That is not a Document Vault backup file.")
        (length,) = struct.unpack(">I", f.read(4))
        return json.loads(f.read(length))


def _open(path: Path, password: str, into) -> tuple[dict, bytes]:
    """Check the password, decrypt the zip into `into`; returns (header, vault key)."""
    with open(path, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise BackupError("That is not a Document Vault backup file.")
        (length,) = struct.unpack(">I", f.read(4))
        header_bytes = f.read(length)
        header = json.loads(header_bytes)
        kdf = header["kdf"]
        backup_key = _derive(password, base64.b64decode(kdf["salt"]), kdf["n"], kdf["r"], kdf["p"])
        wrapped = base64.b64decode(header["key"])
        try:
            vault_key = AESGCM(backup_key).decrypt(wrapped[:12], wrapped[12:], b"vault key")
        except InvalidTag as e:
            raise BackupError("Wrong backup password.") from e
        aead, number, last = AESGCM(backup_key), 0, False
        while True:
            size = f.read(4)
            if not size:
                break
            (n,) = struct.unpack(">I", size)
            nonce, sealed = f.read(12), f.read(n)
            for is_last in (False, True):
                try:
                    into.write(aead.decrypt(nonce, sealed, _aad(header_bytes, number, is_last)))
                    last = is_last
                    break
                except InvalidTag:
                    continue
            else:
                raise BackupError("The backup file is damaged (a part of it does not decrypt).")
            number += 1
            if last:
                break
        if not last or f.read(1):
            raise BackupError("The backup file is incomplete or has extra data at the end.")
    return header, vault_key


def verify_backup(path: str | Path, password: str) -> dict:
    """Decrypt the whole file and look inside it, without restoring anything.
    Raises BackupError if it can't be opened, is damaged or incomplete. Returns what it holds."""
    path = Path(path).expanduser()
    try:
        with tempfile.TemporaryFile() as plain:
            header, _ = _open(path, password, plain)
            plain.seek(0)
            with zipfile.ZipFile(plain) as z:
                bad = z.testzip()
                if bad:
                    raise BackupError(f"{bad} inside the backup is damaged.")
                tables = json.loads(z.read("database.json"))
                files = sum(1 for name in z.namelist() if name.startswith("files/"))
    except (zipfile.BadZipFile, KeyError, ValueError, struct.error) as e:
        raise BackupError(f"The backup can't be read: {e}") from e
    return {"created": header["created"], "documents": len(tables.get("documents", [])), "files": files}


def restore_backup(path: str | Path, password: str, root: str | Path, database_url: str | None = None,
                   replace: bool = False) -> dict:
    """Put a backup back into `root` (and its database). Refuses to overwrite a vault that has
    documents unless replace=True. Returns a summary."""
    path, root = Path(path).expanduser(), Path(root)
    workdir = Path(tempfile.mkdtemp(prefix="vault-restore-"))
    try:
        with open(workdir / "backup.zip", "wb") as zipped:
            header, vault_key = _open(path, password, zipped)
        root.mkdir(parents=True, exist_ok=True)
        engine = connect(database_url or store_module._default_url(root))
        with engine.connect() as conn:
            existing = conn.execute(select(func.count()).select_from(documents)).scalar()
        files_dir = root / "files"
        if (existing or (files_dir.exists() and any(files_dir.iterdir()))) and not replace:
            raise BackupError("This vault already has documents. Restore into an empty vault, or pass --replace "
                              "to throw them away and use the backup's instead.")
        keys.save(header["vault_id"], vault_key)  # first: nothing restored is readable without it
        with zipfile.ZipFile(workdir / "backup.zip") as z:
            tables = json.loads(z.read("database.json"))
            with engine.begin() as conn:
                for table in reversed(metadata.sorted_tables):
                    conn.execute(delete(table))
                for table in metadata.sorted_tables:
                    rows = tables.get(table.name, [])
                    if rows:
                        columns = {c.name: c for c in table.columns}
                        conn.execute(insert(table), [{k: _db_value(columns[k], v) for k, v in r.items() if k in columns}
                                                     for r in rows])
                if engine.dialect.name == "postgresql":  # the id counters continue after the restored rows
                    for table in metadata.sorted_tables:
                        if "id" in table.columns and table.c.id.autoincrement is True:
                            conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                                              f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"))
            if files_dir.exists():
                shutil.rmtree(files_dir)
            files_dir.mkdir(parents=True)
            (root / "vectors.faiss").unlink(missing_ok=True)
            for name in z.namelist():
                if name.startswith("files/") and "/" not in name[6:] and name[6:] not in ("", ".", ".."):
                    (files_dir / name[6:]).write_bytes(z.read(name))
                elif name == "vectors.faiss":
                    (root / "vectors.faiss").write_bytes(z.read(name))
        engine.dispose()
        return {"created": header["created"], "vault_id": header["vault_id"],
                "documents": len(tables.get("documents", [])), "people": len(tables.get("users", []))}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
