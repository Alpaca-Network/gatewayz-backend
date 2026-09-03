"""Tests for the retention cleanup helpers (threat model L11).

usage_records and activity_log have no natural retention pressure and grow
unbounded (unlike chat_completion_requests, which already has a pg_cron
rollup+delete job). credit_transactions is the financial ledger and is never
pruned by this module.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.db.retention import cleanup_activity_log, cleanup_usage_records

# All tests here mock get_supabase_client directly; the fixture below only
# exists so its name ("sb") exempts this module from conftest's
# skip_if_no_database autouse fixture, which otherwise skips any test whose
# path contains "db" unless it requests a fixture named `sb`/`fake_supabase`.
pytestmark = pytest.mark.usefixtures("sb")


@pytest.fixture
def sb():
    return None


def _mock_client(pages: list[list[dict]]):
    """A fake supabase client whose .table(...).select(...)...execute() returns
    successive pages (one per call), and whose .delete()...execute() just
    records the ids it was asked to delete."""
    client = MagicMock()
    select_result_iter = iter(pages)

    def _execute_select():
        result = MagicMock()
        result.data = next(select_result_iter, [])
        return result

    select_chain = client.table.return_value.select.return_value.lt.return_value.limit.return_value
    select_chain.execute.side_effect = _execute_select

    delete_execute = client.table.return_value.delete.return_value.in_.return_value.execute
    delete_execute.return_value = MagicMock()

    return client, delete_execute


@patch("src.db.retention.get_supabase_client")
def test_cleanup_usage_records_deletes_single_partial_batch(mock_get_client):
    client, delete_execute = _mock_client(pages=[[{"id": 1}, {"id": 2}]])
    mock_get_client.return_value = client

    deleted = cleanup_usage_records(retention_days=400, batch_size=5000, max_batches=20)

    assert deleted == 2
    delete_execute.assert_called_once()


@patch("src.db.retention.get_supabase_client")
def test_cleanup_stops_when_batch_is_not_full(mock_get_client):
    """A full page could mean more rows remain; a partial page means we're done."""
    client, delete_execute = _mock_client(pages=[[{"id": i} for i in range(3)]])
    mock_get_client.return_value = client

    deleted = cleanup_activity_log(retention_days=400, batch_size=5000, max_batches=20)

    assert deleted == 3
    assert delete_execute.call_count == 1


@patch("src.db.retention.get_supabase_client")
def test_cleanup_continues_across_full_batches_until_partial(mock_get_client):
    pages = [
        [{"id": i} for i in range(2)],  # full batch (batch_size=2)
        [{"id": i} for i in range(2)],  # full batch again
        [{"id": 100}],  # partial -> stop
    ]
    client, delete_execute = _mock_client(pages=pages)
    mock_get_client.return_value = client

    deleted = cleanup_usage_records(retention_days=400, batch_size=2, max_batches=20)

    assert deleted == 5
    assert delete_execute.call_count == 3


@patch("src.db.retention.get_supabase_client")
def test_cleanup_respects_max_batches_cap(mock_get_client):
    """Even if every batch is full (more rows might remain), stop at max_batches
    per run rather than deleting unboundedly in one pass."""
    full_pages = [[{"id": i} for i in range(2)] for _ in range(50)]
    client, delete_execute = _mock_client(pages=full_pages)
    mock_get_client.return_value = client

    deleted = cleanup_activity_log(retention_days=400, batch_size=2, max_batches=3)

    assert deleted == 6  # 3 batches * 2 rows
    assert delete_execute.call_count == 3


@patch("src.db.retention.get_supabase_client")
def test_cleanup_returns_zero_when_nothing_to_delete(mock_get_client):
    client, delete_execute = _mock_client(pages=[[]])
    mock_get_client.return_value = client

    deleted = cleanup_usage_records(retention_days=400)

    assert deleted == 0
    delete_execute.assert_not_called()


@patch("src.db.retention.get_supabase_client")
def test_cleanup_never_raises_on_db_error(mock_get_client):
    """Retention is a background job — a DB error must not crash the scheduler."""
    mock_get_client.side_effect = RuntimeError("connection refused")

    deleted = cleanup_usage_records(retention_days=400)

    assert deleted == 0
