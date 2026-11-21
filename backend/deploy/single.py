"""Only one copy of the vault may open a vault folder at a time.

The web server (./vault start) and the native apps' engine would otherwise both write to the same database and
search index. The first to start holds a lock on <vault folder>/.vault.lock for as long as it runs; the operating
system releases it when that process ends, even after a crash, so a stale lock never blocks the next start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyOpenError(RuntimeError):
    """Another copy of the vault has this folder open; the message says what to do."""


def hold(folder: str | Path):
    """Lock the vault folder for this process; returns the open lock file (keep it open while the vault runs)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / ".vault.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        handle.close()
        raise AlreadyOpenError(
            f"This vault ({folder}) is already open in another Document Vault window or app. Close that one first "
            "(if it was started with ./vault start: ./vault stop).") from e
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
