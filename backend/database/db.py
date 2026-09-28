"""Connect to the metadata database and create the tables.

The URL comes from --database-url or the VAULT_DATABASE_URL environment variable:

    postgresql+psycopg://localhost/vault      default: PostgreSQL on this computer
    sqlite:///data/index/vault.db             single file, no server (handy for trying things)

Only databases on this computer are accepted, like the local-only LLM rule: the
metadata describes the family's documents.
"""

from __future__ import annotations

import getpass
import os
import re

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError

from backend.database.schema import metadata

DEFAULT_URL = "postgresql+psycopg://localhost/vault"
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


class DatabaseUnavailableError(RuntimeError):
    """Can't reach the database; the message says how to fix it."""


def database_url(cli_value: str | None = None) -> str:
    return cli_value or os.environ.get("VAULT_DATABASE_URL") or DEFAULT_URL


def default_owner(cli_value: str | None = None) -> str | None:
    """An explicitly chosen owner (--owner, then VAULT_OWNER), or None to let the vault decide:
    its existing owner if it has exactly one, otherwise the computer's user name."""
    return cli_value or os.environ.get("VAULT_OWNER") or None


def fallback_owner() -> str:
    try:
        return getpass.getuser() or "family"
    except Exception:  # no user name available (some containers)
        return "family"


def connect(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() not in ("postgresql", "sqlite"):
        raise ValueError(f"Unsupported database {parsed.get_backend_name()!r}; use PostgreSQL or SQLite")
    if parsed.get_backend_name() == "postgresql" and parsed.host not in LOCAL_HOSTS:
        raise ValueError(f"The database must be on this computer (localhost), got host {parsed.host!r}")
    engine = create_engine(url, future=True)
    if parsed.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_foreign_keys(dbapi_conn, _):  # SQLite ignores ON DELETE CASCADE without this
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    try:
        metadata.create_all(engine)
    except OperationalError as e:
        raise DatabaseUnavailableError(explain_connection_error(parsed, e)) from e
    return engine


def _reason(error: OperationalError) -> str:
    """PostgreSQL's own explanation, e.g. 'role "Abhinav" does not exist'."""
    text = str(getattr(error, "orig", error))
    fatal = re.search(r"FATAL:\s*(.+)", text)
    return (fatal.group(1) if fatal else text.strip().splitlines()[0]).strip()


def explain_connection_error(url, error: OperationalError) -> str:
    """Say what went wrong and the exact commands to fix it."""
    where = url.render_as_string(hide_password=True)
    reason = _reason(error)
    lines = [f"Can't connect to the database at {where}.", f"PostgreSQL says: {reason}", ""]
    role = re.search(r'role "([^"]+)" does not exist', reason)
    database = re.search(r'database "([^"]+)" does not exist', reason)
    if role:
        name = role.group(1)
        lines += [
            f'PostgreSQL has no login called "{name}". This happens when the Mac user name changed after',
            "PostgreSQL was installed (Homebrew creates a login named after the user at install time).",
            "Fix, once, replacing OLDNAME with your previous Mac user name:",
            f"  psql -U OLDNAME -d postgres -c 'CREATE ROLE \"{name}\" WITH LOGIN SUPERUSER;'",
            f"  psql -U OLDNAME -d postgres -c 'ALTER DATABASE vault OWNER TO \"{name}\";'",
            "Or keep using the old login:",
            "  export VAULT_DATABASE_URL=postgresql+psycopg://OLDNAME@localhost/vault",
        ]
    elif database:
        lines += ["The database doesn't exist yet. Create it once:", f"  createdb {database.group(1)}"]
    elif re.search(r"password authentication failed", reason, re.I):
        lines += ["The user name or password in the database URL is wrong. Check --database-url / VAULT_DATABASE_URL."]
    else:
        lines += [
            "PostgreSQL doesn't seem to be running. On macOS (once):",
            "  brew install postgresql@16",
            "  brew services start postgresql@16",
            "  createdb vault",
            "Check it with: brew services list",
        ]
    lines += ["", "Or use a single-file database instead: --database-url sqlite:///data/index/vault.db"]
    return "\n".join(lines)
