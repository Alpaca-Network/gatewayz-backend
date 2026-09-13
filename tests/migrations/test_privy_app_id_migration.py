"""
Static idempotency check for
supabase/migrations/20260913000000_privy_app_id.sql (docs/PRIVY_MIGRATION.md).

No SQL runs against a live database in this suite (per repo policy) --
this instead asserts the migration's *shape* guarantees it's safe to apply
more than once: guarded column add, guarded backfill, guarded index. That's
the property CI actually depends on (the migration runner may retry, and a
future migration in the same file family must not clobber rows that
adoption has already stamped with the new app id).
"""

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION_PATH = os.path.join(
    REPO_ROOT, "supabase", "migrations", "20260913000000_privy_app_id.sql"
)


def _read_migration() -> str:
    with open(MIGRATION_PATH, encoding="utf-8") as f:
        return f.read()


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_PATH), MIGRATION_PATH


def test_column_add_is_guarded():
    sql = _read_migration()
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS\s+privy_app_id", sql, re.IGNORECASE
    ), "privy_app_id column add must use IF NOT EXISTS to be re-runnable"


def test_index_create_is_guarded():
    sql = _read_migration()
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS\s+idx_users_privy_app_id", sql, re.IGNORECASE
    ), "the privy_app_id index must use IF NOT EXISTS to be re-runnable"


def test_backfill_only_touches_rows_without_an_existing_value():
    """The backfill UPDATE must be scoped to `privy_app_id IS NULL` -- without
    that guard, re-running the migration (or running it after adoption has
    already stamped some rows with the new app id) would stomp real values
    back to the old app id."""
    sql = _read_migration()
    update_match = re.search(
        r"UPDATE public\.users\s+SET\s+privy_app_id\s*=\s*'[^']+'\s+WHERE\s+(.*?);",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert update_match, "expected a single guarded UPDATE ... SET privy_app_id = ... statement"
    where_clause = update_match.group(1)
    assert re.search(r"privy_app_id\s+IS\s+NULL", where_clause, re.IGNORECASE), (
        "backfill UPDATE must be guarded by `privy_app_id IS NULL` to stay idempotent: "
        f"got WHERE {where_clause!r}"
    )
    assert re.search(r"privy_user_id\s+IS\s+NOT\s+NULL", where_clause, re.IGNORECASE), (
        "backfill UPDATE must only touch rows that actually have a privy_user_id: "
        f"got WHERE {where_clause!r}"
    )
