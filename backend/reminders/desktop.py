"""Mac desktop notifications for reminders, while the vault is running.

Checks when the vault starts and every few hours: for each family member who turned on
"Show on this Mac" (the admin by default), it shows reminders whose day has come, including
ones missed in the last few days while the Mac was off, and remembers what it showed.
Only the reminder text is shown (e.g. "Car Insurance 2025 expires in 7 days"); nothing leaves
this computer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading

from sqlalchemy import select

from backend.database.schema import users
from backend.reminders.reminders import Reminders

logger = logging.getLogger(__name__)
CHECK_EVERY = 3 * 60 * 60  # seconds


def show(title: str, body: str) -> bool:
    """One macOS notification (osascript); elsewhere, just logged."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        quote = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        script = f'display notification "{quote(body)}" with title "Document Vault" subtitle "{quote(title)}"'
        try:
            subprocess.run(["osascript", "-e", script], check=True, timeout=10, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("Couldn't show a notification: %s", e)
            return False
    logger.info("Reminder: %s (%s)", title, body)
    return True


def check_once(store, reminders: Reminders, notify=show) -> int:
    """Show every reminder that's due now, for everyone who wants them on this Mac."""
    with store.engine.connect() as conn:
        user_ids = conn.execute(select(users.c.id).where(users.c.password_hash.is_not(None))).scalars().all()
    shown = 0
    for user_id in user_ids:
        if not reminders.settings(user_id)["desktop"]:
            continue
        view = store.for_user(user_id)
        for reminder in reminders.due_now(view):
            if notify(reminder["title"], f"{view.owner}: {reminder['body']}"):
                reminders.mark_sent(reminder)
                shown += 1
    return shown


def start(store, reminders: Reminders) -> threading.Event:
    """Check now and every CHECK_EVERY seconds in the background; set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                count = check_once(store, reminders)
                if count:
                    logger.info("Showed %d reminder(s) on this Mac", count)
            except Exception:  # a reminder problem must never take the vault down
                logger.exception("Reminder check failed")
            stop.wait(CHECK_EVERY)

    threading.Thread(target=loop, name="reminders", daemon=True).start()
    return stop
