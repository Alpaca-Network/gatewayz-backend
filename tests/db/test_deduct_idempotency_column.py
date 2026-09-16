"""The deduction path's idempotency key must reach the request_id COLUMN.

atomic_deduct_credits took p_request_id and wrote it only into the metadata
JSONB, leaving credit_transactions.request_id NULL on every deduction. Two
guards depended on that column and both were therefore inert:

  * get_transaction_by_request_id() filters on the column, so the explicit
    double-charge check in deduct_credits() never matched.
  * idx_credit_transactions_request_id is a UNIQUE partial index on the column
    WHERE request_id IS NOT NULL -- always NULL constrains nothing.

Production, 2026-09-16: 93,090 api_usage rows, 0 with the column set.
"""

import re
from pathlib import Path

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260916120000_fix_atomic_deduct_request_id.sql"
)


@pytest.fixture
def sb():
    """Named per this repo's conftest convention: tests taking `sb` are exempt
    from the database-availability skip. These read files; they need no DB."""
    return None


@pytest.fixture
def sql() -> str:
    return MIGRATION.read_text()


def _insert_block(sql: str) -> str:
    """The INSERT INTO credit_transactions statement, column list through VALUES."""
    m = re.search(
        r"INSERT INTO credit_transactions\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*RETURNING",
        sql,
        re.S,
    )
    assert m, "could not locate the credit_transactions INSERT"
    return m.group(0)


class TestDeductWritesRequestIdColumn:
    def test_the_migration_exists(self, sb):
        assert MIGRATION.exists(), "the fix migration is missing"

    def test_request_id_is_in_the_insert_column_list(self, sb, sql):
        block = _insert_block(sql)
        columns = block.split("VALUES")[0]
        assert (
            "request_id" in columns
        ), "request_id is not among the inserted columns — the guard stays inert"

    def test_the_parameter_is_the_inserted_value(self, sb, sql):
        block = _insert_block(sql)
        values = block.split("VALUES", 1)[1]
        assert "p_request_id" in values, "p_request_id is never used as an inserted value"

    def test_it_is_still_kept_in_metadata_for_existing_readers(self, sb, sql):
        assert (
            "jsonb_build_object('request_id'" in sql
        ), "metadata must keep carrying request_id so existing readers don't break"

    def test_no_backfill_of_historical_rows(self, sb, sql):
        """Back-filling from metadata could collide with the unique index on
        values that were never actually deduplicated."""
        assert not re.search(
            r"UPDATE\s+credit_transactions", sql, re.I
        ), "this migration must not rewrite historical rows"

    def test_the_function_is_replaced_not_dropped(self, sb, sql):
        assert "CREATE OR REPLACE FUNCTION atomic_deduct_credits" in sql
        assert not re.search(r"DROP\s+FUNCTION\s+atomic_deduct_credits", sql, re.I)


class TestTheGuardsThatDependOnIt:
    def test_lookup_helper_still_filters_on_the_column(self, sb):
        """If this ever moves to metadata, the migration is pointless."""
        src = (Path(__file__).resolve().parents[2] / "src/db/credit_transactions.py").read_text()
        fn = src.split("def get_transaction_by_request_id")[1].split("\ndef ")[0]
        assert '"request_id"' in fn or "'request_id'" in fn
        assert (
            "metadata" not in fn.split("return")[0]
        ), "the lookup must read the column, which is what this migration populates"
