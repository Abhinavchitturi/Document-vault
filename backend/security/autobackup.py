"""Scheduled backups: the encrypted .dvbackup file, written by itself to one or more folders you choose.

Each folder can be a second disk, a USB drive, a NAS share mounted on this Mac, or a cloud-synced
folder (iCloud Drive, Dropbox...). Backups are the same encrypted single files as the manual ones
(backup.py), so a folder only ever holds ciphertext. The vault itself stays on the local disk:
databases don't belong on a network share.

One backup is built per run and copied to every destination, then each copy is opened again and
checked (verify_backup) before older backups are pruned: a copy that doesn't open is removed and
reported, and never replaces a good one. A destination that fails does not stop the others.

Settings live in the vault's vault_meta table ("auto_backup"); the backup password is kept with
the vault key (keys.py). A check runs every few minutes and backs up the destinations that are due;
a failed one is retried after an hour, and each destination's last result is shown in Settings.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.security import keys
from backend.security.backup import MIN_PASSWORD, SUFFIX, BackupError, make_backup, verify_backup

logger = logging.getLogger("vault.autobackup")

META_KEY = "auto_backup"
CHECK_EVERY = 15 * 60  # seconds between checks for a due backup
RETRY_AFTER = timedelta(hours=1)  # after a failure (NAS switched off, drive unplugged...)
INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
DEFAULT_KEEP = 7
MAX_KEEP = 100
MAX_DESTINATIONS = 5
PREFIX = "Document Vault backup "

_LONG_AGO = datetime.min.replace(tzinfo=timezone.utc)  # never backed up: due since forever
_wake = threading.Event()  # set when the settings change, so a newly enabled schedule doesn't wait for the next check
_run_lock = threading.Lock()  # a scheduled and a "Back up now" backup never run together


class AutoBackupError(ValueError):
    """The settings can't be used; the message says why."""


def _destination(path: str) -> dict:
    return {"path": path, "last_attempt": None, "last_success": None, "last_file": None,
            "last_error": None, "last_verified": None, "documents": None}


def _defaults() -> dict:
    return {"enabled": False, "frequency": "daily", "keep": DEFAULT_KEEP, "folders": []}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def load(store) -> dict:
    raw = store._vault._meta(META_KEY)
    config = _defaults()
    if raw:
        try:
            saved = json.loads(raw)
        except ValueError:
            logger.warning("Ignoring unreadable automatic-backup settings")
            saved = {}
        if saved.get("folder") and "folders" not in saved:  # the first version had a single folder
            old = _destination(saved["folder"])
            old.update({k: saved.get(k) for k in ("last_attempt", "last_success", "last_file", "last_error")})
            saved["folders"] = [old]
        for key in ("enabled", "frequency", "keep", "folders"):
            if key in saved:
                config[key] = saved[key]
    config["folders"] = [dict(_destination(d["path"]), **d) for d in config["folders"] if d.get("path")]
    return config


def _save(store, config: dict) -> None:
    store._vault._put_meta(META_KEY, json.dumps(config, sort_keys=True))


def _device(path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None  # not there (a NAS that is off): the backup itself will report that


def _warning(path: str, others: list[str], vault_root) -> str | None:
    """Backups only protect against a disk failure if they are on a different disk from the vault."""
    here = _device(path)
    if here is None:
        return None
    if here == _device(vault_root):
        return "This folder is on the same disk as the vault, so it won't survive a disk failure."
    if any(here == _device(other) for other in others):
        return "This folder is on the same disk as another backup location."
    return None


def status(store) -> dict:
    """What Settings shows: the settings, whether a password is stored (never the password), and each
    destination's last result."""
    config = load(store)
    config["has_password"] = keys.load_backup_password(store._vault.vault_id) is not None
    paths = [d["path"] for d in config["folders"]]
    for i, d in enumerate(config["folders"]):
        d["warning"] = _warning(d["path"], paths[:i], store._vault.root)
    dues = [_due_at(config, d) for d in config["folders"]] if config["enabled"] else []
    config["next_due"] = _iso(max(min(dues), _now())) if dues else None
    config["good_copies"] = sum(1 for d in config["folders"] if d["last_success"] and not d["last_error"])
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


def configure(store, *, enabled: bool, folders: list[str], frequency: str, keep: int, password: str | None = None) -> dict:
    """Validate and save the settings. A password is needed the first time (and replaces the old one when given).
    A folder that is already saved is kept even while its drive is unplugged; a new one must exist and be writable."""
    if frequency not in INTERVALS:
        raise AutoBackupError("Choose daily or weekly.")
    if not 1 <= keep <= MAX_KEEP:
        raise AutoBackupError(f"Keep between 1 and {MAX_KEEP} backups.")
    config = load(store)
    known = {d["path"]: d for d in config["folders"]}
    vault_id = store._vault.vault_id
    if enabled:
        chosen: list[str] = []
        for folder in folders:
            if not folder.strip():
                continue
            path = str(Path(folder).expanduser())
            if path in known and Path(path).is_absolute():
                pass  # saved before: if it is unplugged right now, its backup will say so
            else:
                path = str(_check_folder(folder))
            if path not in chosen:
                chosen.append(path)
        if not chosen:
            raise AutoBackupError("Choose the folder the backups go to.")
        if len(chosen) > MAX_DESTINATIONS:
            raise AutoBackupError(f"Use at most {MAX_DESTINATIONS} backup locations.")
        if password:
            if len(password) < MIN_PASSWORD:
                raise AutoBackupError(f"Use a backup password of at least {MIN_PASSWORD} characters.")
            keys.save_backup_password(vault_id, password)
        elif keys.load_backup_password(vault_id) is None:
            raise AutoBackupError(f"Choose a backup password (at least {MIN_PASSWORD} characters). "
                                  "Without it the backups can't be opened, so keep a copy somewhere safe.")
        fresh = not config["enabled"]
        config["folders"] = [known.get(path) or _destination(path) for path in chosen]
        if fresh:
            for d in config["folders"]:
                d["last_error"] = None
    config.update(enabled=enabled, frequency=frequency, keep=keep)
    _save(store, config)
    _wake.set()
    return status(store)


def _due_at(config: dict, dest: dict) -> datetime:
    """When this destination's next backup is due: a full interval after its last good one, or an hour after a failure."""
    interval = INTERVALS.get(config["frequency"], INTERVALS["daily"])
    due = _parse(dest["last_success"]) + interval if dest["last_success"] else _LONG_AGO
    attempt = _parse(dest["last_attempt"])
    if attempt and dest["last_error"]:  # the last try failed: don't hammer a missing NAS
        due = max(due, attempt + RETRY_AFTER)
    return due


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def is_due(config: dict, now: datetime | None = None) -> bool:
    """Is a backup due at any destination?"""
    return config["enabled"] and any((now or _now()) >= _due_at(config, d) for d in config["folders"])


def _prune(folder: Path, keep: int) -> int:
    """Delete the oldest of OUR backup files beyond `keep`. Never touches other files."""
    ours = sorted((p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()), key=lambda p: p.name, reverse=True)
    for old in ours[keep:]:
        try:
            old.unlink()
        except OSError as e:
            logger.warning("Could not remove old backup %s: %s", old.name, e)
    return max(0, len(ours) - keep)


def _deliver(built: Path, folder: Path, password: str, keep: int, dest: dict) -> None:
    """Copy the backup into the folder, open the copy again, and only then prune. A copy that fails its check is removed."""
    target = folder / built.name
    partial = folder / (built.name + ".partial")
    try:
        shutil.copyfile(built, partial)
        os.replace(partial, target)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
    try:
        found = verify_backup(target, password)  # read back from where it now lives, not from our own copy
    except BackupError as e:
        target.unlink(missing_ok=True)
        raise BackupError(f"The copy written to {folder} did not pass its check and was removed ({e})") from e
    _prune(folder, keep)
    dest.update(last_success=_iso(_now()), last_verified=_iso(_now()), last_file=target.name,
                last_error=None, documents=found["documents"])


def run_now(store, *, n: int = 2 ** 17, only_due: bool = False) -> dict:
    """Make a backup and put a checked copy in each destination (only the due ones if `only_due`).
    Returns the new status. Never raises for a backup problem: each failure is recorded for Settings to show."""
    with _run_lock:
        config = load(store)
        now = _now()
        targets = [d for d in config["folders"] if not only_due or now >= _due_at(config, d)]
        if targets:
            _run(store, config, targets, n)
            _save(store, config)
    return status(store)


def _run(store, config: dict, targets: list[dict], n: int) -> None:
    stamp = _iso(_now())
    for dest in targets:
        dest["last_attempt"] = stamp
    password = keys.load_backup_password(store._vault.vault_id)
    if not password:
        for dest in targets:
            dest["last_error"] = "No backup password is saved. Open Settings → Security and enter one."
        return
    ready = []
    for dest in targets:
        try:
            ready.append((dest, _check_folder(dest["path"])))  # never creates a folder: an unmounted NAS must fail loudly
        except AutoBackupError as e:
            dest["last_error"] = str(e)
            logger.warning("Automatic backup to %s failed: %s", dest["path"], e)
    if not ready:
        return
    with tempfile.TemporaryDirectory(prefix="vault-backup-") as work:
        try:
            built = make_backup(store, work, password, n=n)
        except Exception as e:  # a backup problem must never take the vault down
            logger.exception("Could not make the backup")
            for dest, _ in ready:
                dest["last_error"] = f"Could not make the backup: {e}"
            return
        for dest, folder in ready:
            try:
                _deliver(built, folder, password, max(1, int(config["keep"])), dest)
                logger.info("Automatic backup written and checked: %s", folder / built.name)
            except (BackupError, OSError) as e:
                dest["last_error"] = str(e)
                logger.warning("Automatic backup to %s failed: %s", folder, e)
            except Exception as e:
                dest["last_error"] = f"Unexpected problem: {e}"
                logger.exception("Automatic backup to %s failed", folder)


def start(store) -> threading.Event:
    """Check now and every CHECK_EVERY seconds in the background; set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                if is_due(load(store)):
                    run_now(store, only_due=True)
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
