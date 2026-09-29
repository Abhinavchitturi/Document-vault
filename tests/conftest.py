"""Run the test suite on SQLite (default) or on a real PostgreSQL server.

    pytest -q                                                    # SQLite files in temp folders
    VAULT_TEST_DATABASE_URL=postgresql+psycopg://localhost/postgres pytest -q

With PostgreSQL, each vault a test creates gets its own throwaway database
(vt_<hash>), dropped when the test session ends. The URL must point at a server
where the user may CREATE DATABASE; the database named in it is only used to
issue those commands.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.vector_store import store as store_module

ADMIN_URL = os.environ.get("VAULT_TEST_DATABASE_URL")
os.environ.setdefault("VAULT_FACTS_READER", "rules")  # never reach for Needle's download during tests
_created: list[str] = []


def _admin():
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def _postgres_url_for(root) -> str:
    name = "vt_" + hashlib.sha1(str(root).encode()).hexdigest()[:16]
    if name not in _created:
        with _admin().connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        _created.append(name)
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(autouse=True)
def _database_backend(monkeypatch):
    if ADMIN_URL:
        monkeypatch.setattr(store_module, "_default_url", _postgres_url_for)
    yield


def pytest_sessionfinish(session, exitstatus):
    if ADMIN_URL and _created:
        with _admin().connect() as conn:
            for name in _created:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
