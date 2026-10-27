"""SQL tests against a throwaway Postgres with every migration applied.

These run only in .github/workflows/migrations-db-test.yml, which starts the
Supabase local database (`supabase db start`: real Supabase roles, extensions
and the full supabase/migrations chain) and sets GATEWAYZ_TEST_DATABASE_URL.
Without that variable every test here is skipped, so the normal suite and a
developer laptop never touch a database (repo policy: no SQL against a live DB).
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get("GATEWAYZ_TEST_DATABASE_URL")


def pytest_collection_modifyitems(config, items):
    if DSN:
        return
    skip = pytest.mark.skip(reason="GATEWAYZ_TEST_DATABASE_URL not set (CI-only SQL tests)")
    for item in items:
        if "tests/sql/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


@pytest.fixture
def db():
    """A superuser connection inside a transaction that is always rolled back."""
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(DSN, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def connect():
    """Fresh autocommit connections, for tests that need real concurrency.

    Rows they create must be cleaned up by the test itself.
    """
    psycopg = pytest.importorskip("psycopg")
    opened = []

    def _open():
        c = psycopg.connect(DSN, autocommit=True)
        opened.append(c)
        return c

    yield _open
    for c in opened:
        c.close()
