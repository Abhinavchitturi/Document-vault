"""Vault security from a terminal on the Mac (Phase 11).

    python -m backend.security.cli status                # is the vault encrypted, where is the key
    python -m backend.security.cli recovery-key          # show the recovery key: write it on paper
    python -m backend.security.cli restore-key           # put the key back (new Mac, Keychain lost)
    python -m backend.security.cli backup ~/Desktop      # one encrypted file with everything
    python -m backend.security.cli restore "<file>.dvbackup" [--replace]
    python -m backend.security.cli verify "<file>.dvbackup"   # opens it fully, restores nothing
    python -m backend.security.cli activity [--limit 50] # recent sign-ins and document activity

Passwords and the recovery key are typed at a hidden prompt, never on the command line.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from sqlalchemy import select

from backend.database.db import DatabaseUnavailableError, connect, database_url
from backend.database.schema import vault_meta
from backend.security import keys
from backend.security.backup import MIN_PASSWORD, BackupError, make_backup, read_header, restore_backup, verify_backup
from backend.security.crypto import key_from_recovery_code, recovery_code


def _meta(engine, key: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(select(vault_meta.c.value).where(vault_meta.c.key == key)).scalar()


def _open_store(args):
    """The vault, opened as its admin (or first member): never creates or renames anyone."""
    from backend.database.schema import users
    from backend.vector_store.store import DocumentStore

    try:
        url = database_url(args.database_url)
        engine = connect(url)
        with engine.connect() as conn:
            owner = conn.execute(select(users.c.name).order_by(users.c.is_admin.desc(), users.c.id).limit(1)).scalar()
        engine.dispose()
        return DocumentStore(args.index_dir, url, owner=owner)
    except (DatabaseUnavailableError, keys.KeyMissingError) as e:
        raise SystemExit(str(e)) from e


def _new_backup_password() -> str:
    print(f"Choose a backup password (at least {MIN_PASSWORD} characters). Without it the backup can't be opened,")
    print("so keep it somewhere safe, apart from the backup file.")
    first = getpass.getpass("Backup password: ")
    if getpass.getpass("Type it again: ") != first:
        raise SystemExit("The two passwords don't match.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="default: $VAULT_DATABASE_URL or postgresql+psycopg://localhost/vault")
    parser.add_argument("--index-dir", default="data/index", help="the vault folder (as for backend.api.app)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="is the vault encrypted and where is its key")
    sub.add_parser("recovery-key", help="show the recovery key (write it down, keep it safe)")
    sub.add_parser("restore-key", help="put the vault key back from the recovery key")
    backup = sub.add_parser("backup", help="write an encrypted backup file into a folder")
    backup.add_argument("folder", nargs="?", default=str(Path.home() / "Desktop"))
    restore = sub.add_parser("restore", help="restore a backup file into this vault")
    restore.add_argument("file")
    restore.add_argument("--replace", action="store_true", help="throw away the documents already in this vault")
    verify = sub.add_parser("verify", help="check that a backup file opens and is complete (restores nothing)")
    verify.add_argument("file")
    activity = sub.add_parser("activity", help="recent sign-ins and document activity (everyone)")
    activity.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    if args.command == "restore":
        if not Path(args.file).expanduser().is_file():
            raise SystemExit(f"No such file: {args.file}")
        try:
            header = read_header(Path(args.file).expanduser())
        except BackupError as e:
            raise SystemExit(str(e)) from e
        print(f"Backup made {header['created']}.")
        try:
            done = restore_backup(args.file, getpass.getpass("Backup password: "), args.index_dir,
                                  database_url(args.database_url), replace=args.replace)
        except (BackupError, DatabaseUnavailableError) as e:
            raise SystemExit(str(e)) from e
        print(f"Restored {done['documents']} documents and {done['people']} family members.")
        print("Start the vault as usual. Everyone signs in again; paired phones keep working.")
        return

    if args.command == "verify":
        if not Path(args.file).expanduser().is_file():
            raise SystemExit(f"No such file: {args.file}")
        try:
            found = verify_backup(args.file, getpass.getpass("Backup password: "))
        except BackupError as e:
            raise SystemExit(f"NOT OK: {e}") from e
        print(f"OK: made {found['created']}, {found['documents']} documents, {found['files']} stored files.")
        return

    if args.command == "restore-key":
        try:
            engine = connect(database_url(args.database_url))
        except DatabaseUnavailableError as e:
            raise SystemExit(str(e)) from e
        vault_id = _meta(engine, "vault_id")
        if vault_id is None:
            raise SystemExit("This database has no encrypted vault yet: nothing to restore a key for.")
        try:
            key = key_from_recovery_code(getpass.getpass("Recovery key (letters and digits, dashes optional): "))
        except ValueError as e:
            raise SystemExit(str(e)) from e
        from backend.security.crypto import Cipher, DecryptError
        from backend.vector_store.store import KEY_CHECK

        check = _meta(engine, "key_check")
        try:
            ok = check is None or Cipher(key).open_text(check) == KEY_CHECK
        except DecryptError:
            ok = False
        if not ok:
            raise SystemExit("That recovery key does not open this vault (it may belong to another one).")
        print(f"Key saved to {keys.save(vault_id, key)}. Start the vault as usual.")
        return

    store = _open_store(args)
    vault = store._vault
    if args.command == "status":
        print(f"Vault folder: {Path(args.index_dir).resolve()}")
        print("Encrypted: yes (AES-256-GCM): files, scans, search index, names, passages, ID numbers")
        print(f"Key kept in: {keys.where(vault.vault_id)}")
    elif args.command == "recovery-key":
        print("Recovery key for this vault. Write it on paper and keep it with your important papers.")
        print("Anyone with it AND a copy of the vault folder can read the documents, so never put it in a")
        print("photo, chat or cloud note.\n")
        print("    " + recovery_code(keys.load(vault.vault_id)) + "\n")
    elif args.command == "backup":
        try:
            path = make_backup(store, args.folder, _new_backup_password())
        except BackupError as e:
            raise SystemExit(str(e)) from e
        from backend.security.access_log import AccessLog

        AccessLog(store.engine, store.cipher).record("backup_made", {"name": "terminal", "device_name": "Mac terminal"}, detail=path.name)
        print(f"Backup written: {path}")
    elif args.command == "activity":
        from backend.security.access_log import AccessLog

        for e in reversed(AccessLog(store.engine, store.cipher).entries(limit=args.limit)):
            flag = "" if e["ok"] else "  ✗"
            what = f"{e['what']}{': ' + e['detail'] if e['detail'] else ''}"
            print(f"{e['at'][:16].replace('T', ' ')}  {e['user_name'] or '?':<12} {e['via']:<18} {what}{flag}")


if __name__ == "__main__":
    main()
