"""Scheduled backups: the encrypted .dvbackup file, written by itself to a folder you choose.

The folder can be a second disk, a USB drive or a NAS share mounted on this Mac. Backups are
the same encrypted single files as the manual ones (backup.py), so the folder only ever holds
ciphertext. The vault itself stays on the local disk: databases don't belong on a network share.

Settings live in the vault's vault_meta table ("auto_backup"); the backup password is kept with
the vault key (keys.py). A check runs every few minutes and makes a backup when one is due; a
failed one is retried after an hour, and the last result is shown in Settings.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.security import keys
from backend.security.backup import MIN_PASSWORD, SUFFIX, BackupError, make_backup, read_header

logger = logging.getLogger("vault.autobackup")

META_KEY = "auto_backup"
CHECK_EVERY = 15 * 60  # seconds between checks for a due backup
RETRY_AFTER = timedelta(hours=1)  # after a failure (NAS switched off, drive unplugged...)
INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
DEFAULT_KEEP = 7
MAX_KEEP = 100
PREFIX = "Document Vault backup "

_wake = threading.Event()  # set when the settings change, so a newly enabled schedule doesn't wait for the next check
_run_lock = threading.Lock()  # a scheduled and a "Back up now" backup never run together


class AutoBackupError(ValueError):
    """The settings can't be used; the message says why."""


def _defaults() -> dict:
    return {"enabled": False, "folder": "", "frequency": "daily", "keep": DEFAULT_KEEP,
            "last_attempt": None, "last_success": None, "last_file": None, "last_error": None}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def load(store) -> dict:
    raw = store._vault._meta(META_KEY)
    config = _defaults()
    if raw:
        try:
            config.update(json.loads(raw))
        except ValueError:
            logger.warning("Ignoring unreadable automatic-backup settings")
    return config


def _save(store, config: dict) -> None:
    store._vault._put_meta(META_KEY, json.dumps(config, sort_keys=True))


def status(store) -> dict:
    """What Settings shows: the settings, whether a password is stored (never the password), and the last result."""
    config = load(store)
    config["has_password"] = keys.load_backup_password(store._vault.vault_id) is not None
    config["next_due"] = _iso(_due_at(config)) if config["enabled"] and _due_at(config) else None
    return config


def _check_folder(folder: str) -> Path:
    if not folder.strip():
        raise AutoBackupError("Choose the folder the backups go to.")
    path = Path(folder).expanduser()
    if not path.is_absolute():
        raise AutoBackupError("Give the full path of the folder, for example /Volumes/NAS/Backups.")
    if not path.is_dir():
        raise AutoBackupError(f"{path} isn't there. Is the drive or NAS connected, and the folder created?")
    probe = path / ".vault-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise AutoBackupError(f"Can't write to {path}: {e.strerror or e}") from e
    return path


def configure(store, *, enabled: bool, folder: str, frequency: str, keep: int, password: str | None = None) -> dict:
    """Validate and save the settings. A password is needed the first time (and replaces the old one when given)."""
    if frequency not in INTERVALS:
        raise AutoBackupError("Choose daily or weekly.")
    if not 1 <= keep <= MAX_KEEP:
        raise AutoBackupError(f"Keep between 1 and {MAX_KEEP} backups.")
    vault_id = store._vault.vault_id
    if enabled:
        folder = str(_check_folder(folder))
        if password:
            if len(password) < MIN_PASSWORD:
                raise AutoBackupError(f"Use a backup password of at least {MIN_PASSWORD} characters.")
            keys.save_backup_password(vault_id, password)
        elif keys.load_backup_password(vault_id) is None:
            raise AutoBackupError(f"Choose a backup password (at least {MIN_PASSWORD} characters). "
                                  "Without it the backups can't be opened, so keep a copy somewhere safe.")
    config = load(store)
    was_enabled = config["enabled"]
    config.update(enabled=enabled, folder=folder if enabled else config["folder"], frequency=frequency, keep=keep)
    if enabled and not was_enabled:
        config["last_error"] = None
    _save(store, config)
    _wake.set()
    return status(store)


def _due_at(config: dict) -> datetime | None:
    """When the next backup is due: a full interval after the last good one, or an hour after a failure."""
    if not config["enabled"]:
        return None
    interval = INTERVALS.get(config["frequency"], INTERVALS["daily"])
    due = _parse(config["last_success"]) + interval if config["last_success"] else _now()
    attempt = _parse(config["last_attempt"])
    if attempt and config["last_error"]:  # the last try failed: don't hammer a missing NAS
        due = max(due, attempt + RETRY_AFTER)
    return due


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def is_due(config: dict, now: datetime | None = None) -> bool:
    due = _due_at(config)
    return due is not None and (now or _now()) >= due


def _prune(folder: Path, keep: int) -> int:
    """Delete the oldest of OUR backup files beyond `keep`. Never touches other files."""
    ours = sorted((p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()), key=lambda p: p.name, reverse=True)
    for old in ours[keep:]:
        try:
            old.unlink()
        except OSError as e:
            logger.warning("Could not remove old backup %s: %s", old.name, e)
    return max(0, len(ours) - keep)


def run_now(store, *, n: int = 2 ** 17) -> dict:
    """Make a backup into the chosen folder, check it can be read, drop the oldest ones. Returns the new status.
    Never raises for a backup problem: the failure is recorded for Settings to show."""
    with _run_lock:
        config = load(store)
        config["last_attempt"] = _iso(_now())
        try:
            password = keys.load_backup_password(store._vault.vault_id)
            if not password:
                raise AutoBackupError("No backup password is saved. Open Settings → Security and enter one.")
            folder = _check_folder(config["folder"])  # never creates the folder: an unmounted NAS must fail loudly
            target = make_backup(store, folder, password, n=n)
            read_header(target)  # the file we just wrote must open as a vault backup
            _prune(folder, max(1, int(config["keep"])))
            config.update(last_success=_iso(_now()), last_file=target.name, last_error=None)
            logger.info("Automatic backup written: %s", target)
        except (AutoBackupError, BackupError, OSError) as e:
            config["last_error"] = str(e)
            logger.warning("Automatic backup failed: %s", e)
        except Exception as e:  # a backup problem must never take the vault down
            config["last_error"] = f"Unexpected problem: {e}"
            logger.exception("Automatic backup failed")
        _save(store, config)
    return status(store)


def start(store) -> threading.Event:
    """Check now and every CHECK_EVERY seconds in the background; set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                if is_due(load(store)):
                    run_now(store)
            except Exception:
                logger.exception("Automatic backup check failed")
            _wake.wait(CHECK_EVERY)
            _wake.clear()

    threading.Thread(target=loop, name="autobackup", daemon=True).start()
    return stop


def stop_and_wake(stop: threading.Event) -> None:
    """Stop the background check now instead of after its current wait."""
    stop.set()
    _wake.set()
