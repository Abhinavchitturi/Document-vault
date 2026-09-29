"""Manage sign-ins from a terminal on this computer (for when nobody can sign in).

    python -m backend.auth.cli list
    python -m backend.auth.cli reset-password abhinav
    python -m backend.auth.cli add-member sunita [--admin]

Passwords are typed at a hidden prompt, never on the command line (it would stay in your
shell history). Anyone who can run this can already read the vault's files, so it needs no
password of its own.
"""

from __future__ import annotations

import argparse
import getpass

from backend.auth.accounts import Accounts, AuthError
from backend.database.db import DatabaseUnavailableError, connect, database_url


def _ask_password() -> str:
    first = getpass.getpass("New password: ")
    if getpass.getpass("Type it again: ") != first:
        raise SystemExit("The two passwords don't match.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None, help="default: $VAULT_DATABASE_URL or postgresql+psycopg://localhost/vault")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="family members and whether they can sign in")
    reset = sub.add_parser("reset-password", help="set a new password for someone")
    reset.add_argument("name")
    add = sub.add_parser("add-member", help="create an account")
    add.add_argument("name")
    add.add_argument("--admin", action="store_true", help="can add members and reset passwords")
    args = parser.parse_args()

    try:
        accounts = Accounts(connect(database_url(args.database_url)))
    except DatabaseUnavailableError as e:
        raise SystemExit(str(e)) from e
    try:
        if args.command == "list":
            for u in accounts.list_users():
                flags = ", ".join(f for f, on in (("admin", u["is_admin"]), ("no password yet", not u["can_sign_in"])) if on)
                print(f"{u['name']}{f'  ({flags})' if flags else ''}")
        elif args.command == "reset-password":
            user = accounts.find_user(args.name)
            if user is None:
                raise SystemExit(f"No family member called {args.name!r}. See: python -m backend.auth.cli list")
            accounts.set_password(user["id"], _ask_password())
            print(f"Password changed for {user['name']}. They are signed out everywhere.")
        elif args.command == "add-member":
            user = accounts.add_member(args.name, _ask_password(), is_admin=args.admin)
            print(f"Added {user['name']}{' (admin)' if user['is_admin'] else ''}.")
    except AuthError as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
