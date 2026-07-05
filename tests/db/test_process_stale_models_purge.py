"""Tests for the hard-purge path in process_stale_models.

The stale-model flow soft-deactivates models a provider stops listing, but
without a purge those rows live forever and the `models` table only grows.
`purge_after_days` hard-deletes models that are BOTH already deactivated
(is_active=False) AND unseen for longer than the cutoff — keeping the table
light without ever touching an active model.

These are pure unit tests: the Supabase client is replaced with a tiny fake
that records the fluent chain (select/eq/lt/in_/range/delete/execute) so we can
assert exactly which rows get deleted.
"""

from datetime import UTC, datetime, timedelta

import pytest


class FakeQuery:
    """Records a fluent PostgREST-style chain and returns canned rows.

    `select` chains resolve to `self._rows`; `delete` chains record deleted ids
    on the parent client. Filters are captured but not applied — each test
    hands the fake exactly the rows the corresponding query should return.
    """

    def __init__(self, client, rows, mode="select"):
        self._client = client
        self._rows = rows
        self._mode = mode
        self._in_ids = None

    def select(self, *a, **k):
        return self

    def delete(self, *a, **k):
        return FakeQuery(self._client, self._rows, mode="delete")

    def update(self, *a, **k):
        return FakeQuery(self._client, self._rows, mode="update")

    def eq(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def in_(self, _col, ids):
        self._in_ids = list(ids)
        return self

    def range(self, *a, **k):
        return self

    def execute(self):
        if self._mode == "delete":
            self._client.deleted_ids.extend(self._in_ids or [])
        return type("Resp", (), {"data": self._rows})()


class FakeClient:
    """Serves a scripted list of row-batches, one per `.table()` call.

    process_stale_models issues its queries in a fixed order; we enqueue the
    rows each should return. Any `.delete()` records ids on `deleted_ids`.
    """

    def __init__(self, batches):
        self._batches = list(batches)
        self.deleted_ids: list[int] = []
        self._i = 0

    def table(self, _name):
        rows = self._batches[self._i] if self._i < len(self._batches) else []
        self._i += 1
        return FakeQuery(self, rows)


@pytest.fixture
def sb():
    """Presence bypasses the autouse DB-skip in tests/conftest.py — these are
    pure unit tests with the Supabase client fully faked."""
    return None


def _run(batches, **kwargs):
    from unittest.mock import patch

    fake = FakeClient(batches)
    with patch("src.db.models_catalog_db.get_client_for_query", return_value=fake):
        from src.db.models_catalog_db import process_stale_models

        result = process_stale_models(provider_id=7, seen_provider_model_ids=set(), **kwargs)
    return result, fake


def test_purge_disabled_by_default_never_deletes(sb):
    """With no purge_after_days, no delete is issued (backward compatible)."""
    # One active model, unseen → deactivated; no purge query runs.
    active = [{"id": 1, "provider_model_id": "gone", "consecutive_missing_count": 2}]
    result, fake = _run([active])
    assert result["deactivated"] == 1
    assert result.get("purged", 0) == 0
    assert fake.deleted_ids == []


def test_purge_deletes_long_deactivated_models(sb):
    """Deactivated + unseen past the cutoff → hard-deleted."""
    old = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    active_batch = []  # no active models this sync
    purge_batch = [
        {"id": 10, "last_seen_in_provider_at": old},
        {"id": 11, "last_seen_in_provider_at": old},
    ]
    # batches: [fetch active] then [fetch purge-eligible inactive]
    result, fake = _run([active_batch, purge_batch], purge_after_days=30)
    assert result["purged"] == 2
    assert sorted(fake.deleted_ids) == [10, 11]


def test_purge_runs_even_when_no_active_models(sb):
    """Providers with only dead rows must still get purged."""
    purge_batch = [{"id": 99, "last_seen_in_provider_at": "2020-01-01T00:00:00+00:00"}]
    result, fake = _run([[], purge_batch], purge_after_days=30)
    assert result["purged"] == 1
    assert fake.deleted_ids == [99]


def test_purge_nothing_eligible(sb):
    """Cutoff set but no row past it → no delete."""
    result, fake = _run([[], []], purge_after_days=30)
    assert result["purged"] == 0
    assert fake.deleted_ids == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
