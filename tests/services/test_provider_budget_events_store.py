"""The persistence layer behind the /admin/status provider_budget block.

Two things are pinned here: the exact shape of what we ask Supabase for (this repo has
been burned repeatedly by queries naming columns the database does not have), and that a
failed read raises instead of returning an empty list.
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.db.provider_budget_events import (
    ProviderBudgetEventsUnavailable,
    list_recent_budget_events,
    record_budget_event,
)

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260916210000_provider_budget_events.sql"
)


class _FakeQuery:
    def __init__(self, calls, rows):
        self._calls = calls
        self._rows = rows

    def select(self, columns):
        self._calls["select"] = columns
        return self

    def gte(self, column, value):
        self._calls["gte"] = (column, value)
        return self

    def order(self, column, desc=False):
        self._calls["order"] = (column, desc)
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeClient:
    def __init__(self, calls, rows=None, raises=None):
        self._calls = calls
        self._rows = rows if rows is not None else []
        self._raises = raises

    def table(self, name):
        self._calls["table"] = name
        if self._raises:
            raise self._raises
        return _FakeQuery(self._calls, self._rows)

    def rpc(self, name, params):
        self._calls["rpc"] = (name, params)
        if self._raises:
            raise self._raises
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=None))


def _with_client(calls, **kwargs):
    return patch(
        "src.config.supabase_config.get_supabase_client",
        return_value=_FakeClient(calls, **kwargs),
    )


class TestWrite:
    def test_the_upsert_rpc_receives_every_parameter_it_declares(self):
        calls: dict = {}
        with _with_client(calls):
            record_budget_event("anthropic", "credit_balance_low", "claude-sonnet-5", 7)

        name, params = calls["rpc"]
        assert name == "record_provider_budget_event"
        assert params == {
            "p_provider": "anthropic",
            "p_reason": "credit_balance_low",
            "p_model": "claude-sonnet-5",
            # The coalesced count, not 1: throttling the write must not undercount. An
            # app-side read-modify-write would also lose counts across Railway instances,
            # which is why this is a single atomic RPC.
            "p_increment": 7,
        }

    def test_the_rpc_signature_in_the_migration_matches_what_we_send(self):
        sql = MIGRATION.read_text()
        declared = set(re.findall(r"^\s+(p_\w+)\s+\w+", sql, flags=re.MULTILINE))
        calls: dict = {}
        with _with_client(calls):
            record_budget_event("anthropic", "credit_balance_low", "m", 1)
        assert set(calls["rpc"][1]) == declared

    def test_a_failed_write_propagates_so_the_caller_can_requeue(self):
        calls: dict = {}
        with _with_client(calls, raises=ValueError("PGRST202 no such function")):
            with pytest.raises(ValueError):
                record_budget_event("anthropic", "credit_balance_low", "m", 1)


class TestRead:
    def test_the_query_shape(self):
        calls: dict = {}
        with _with_client(calls, rows=[{"provider": "anthropic"}]):
            rows = list_recent_budget_events(within_hours=6)

        assert calls["table"] == "provider_budget_events"
        assert calls["gte"][0] == "last_seen_at"
        assert calls["order"] == ("last_seen_at", True)
        assert rows == [{"provider": "anthropic"}]

    def test_every_selected_column_exists_in_the_migration(self):
        # The phantom-column class of bug: SELECT a column the database dropped, get a
        # 42703, and let a broad except turn it into a plausible-looking empty result.
        sql = MIGRATION.read_text()
        create = sql.split("CREATE TABLE IF NOT EXISTS public.provider_budget_events", 1)[1]
        create = create.split(");", 1)[0]
        defined = set(
            re.findall(r"^\s+(\w+)\s+(?:TEXT|BIGSERIAL|BIGINT|TIMESTAMPTZ)", create, re.M)
        )

        calls: dict = {}
        with _with_client(calls):
            list_recent_budget_events()
        selected = {c.strip() for c in calls["select"].split(",")}

        assert selected <= defined, f"phantom column(s): {selected - defined}"

    def test_a_broken_read_raises_rather_than_returning_an_empty_list(self):
        calls: dict = {}
        with _with_client(calls, raises=ValueError("PGRST205 relation does not exist")):
            with pytest.raises(ProviderBudgetEventsUnavailable):
                list_recent_budget_events()

    def test_no_rows_is_an_empty_list_not_an_error(self):
        calls: dict = {}
        with _with_client(calls, rows=None):
            assert list_recent_budget_events() == []


class TestMigrationConventions:
    def test_it_is_idempotent(self):
        # Migrations are CI-applied on merge and may be re-run.
        sql = MIGRATION.read_text()
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "CREATE INDEX IF NOT EXISTS" in sql
        assert "CREATE OR REPLACE FUNCTION" in sql

    def test_it_uses_no_concurrently(self):
        # `supabase db push` wraps the push in a transaction, so CREATE INDEX
        # CONCURRENTLY cannot run here. Repo convention, see the Sep 15 2026 note.
        assert "CONCURRENTLY" not in MIGRATION.read_text().upper()

    def test_the_table_is_service_role_only(self):
        sql = MIGRATION.read_text()
        assert "ENABLE ROW LEVEL SECURITY" in sql
        assert "REVOKE ALL ON public.provider_budget_events FROM anon, authenticated;" in sql
        assert "GRANT ALL ON public.provider_budget_events TO service_role;" in sql
