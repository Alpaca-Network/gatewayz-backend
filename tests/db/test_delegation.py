"""Tests for src/db/delegation.py -- in particular the reads that must fail
CLOSED (None, never 0 / {}) because money decisions are made on them."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import src.db.delegation as db


@pytest.fixture
def sb():
    """Opt out of conftest's skip_if_no_database: the client is mocked."""
    return None


def _query(pages):
    """A chained query mock whose successive .execute() calls return `pages`."""
    query = MagicMock()
    for method in ("select", "eq", "in_", "gte", "lt", "order", "limit", "range", "insert"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [MagicMock(data=p) for p in pages]
    client = MagicMock()
    client.table.return_value = query
    return client, query


def _broken():
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    return client


def test_sum_granted_credits_pages_through_everything(sb, monkeypatch):
    monkeypatch.setattr(db, "_PAGE", 2)
    client, query = _query([[{"credits": "1"}, {"credits": "2"}], [{"credits": "3.5"}]])
    with patch.object(db, "get_supabase_client", return_value=client):
        assert db.sum_granted_credits("eth") == Decimal("6.5")
    assert query.range.call_args_list[1].args == (2, 3)
    query.in_.assert_called_with("status", ["pending", "paid"])


def test_sums_are_none_not_zero_on_error(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.sum_granted_credits("eth") is None
        assert db.sum_revenue_usd("ada") is None
        assert db.list_revenue_period_keys("ada") is None


def test_controls_are_none_on_error(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.get_controls() is None


def test_measurement_read_is_none_on_error_and_values_otherwise(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.get_measurements_for_date("0xabc", "eth", date(2026, 10, 7)) is None
    client, query = _query([[{"usd_value": "0"}, {"usd_value": "12.5"}]])
    with patch.object(db, "get_supabase_client", return_value=client):
        assert db.get_measurements_for_date("0xABC", "eth", date(2026, 10, 7)) == [
            Decimal(0),
            Decimal("12.5"),
        ]
    query.eq.assert_any_call("wallet_address", "0xabc")
    query.gte.assert_called_with("taken_at", "2026-10-07T00:00:00+00:00")
    query.lt.assert_called_with("taken_at", "2026-10-08T00:00:00+00:00")


def test_record_measurement_writes_zero(sb):
    client, query = _query([[{"id": 1}]])
    taken = datetime(2026, 10, 8, 6, tzinfo=UTC)
    with patch.object(db, "get_supabase_client", return_value=client):
        assert db.record_measurement("0xABC", "eth", 0, Decimal(0), taken) is True
    assert query.insert.call_args.args[0] == {
        "wallet_address": "0xabc",
        "asset": "eth",
        "amount_raw": "0",
        "usd_value": "0",
        "taken_at": taken.isoformat(),
    }


def test_create_accrual_is_pending(sb):
    client, query = _query([[{"id": 9, "status": "pending"}]])
    with patch.object(db, "get_supabase_client", return_value=client):
        row = db.create_accrual(
            "0xA", "ada", date(2026, 10, 7), Decimal(1), Decimal(2), Decimal(3), 5
        )
    assert row["id"] == 9
    payload = query.insert.call_args.args[0]
    assert payload["status"] == "pending" and payload["ledger_request_id"] is None
    assert payload["wallet_address"] == "0xa" and payload["user_id"] == 5


def test_list_accruals_for_wallet_date_is_none_on_error(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.list_accruals_for_wallet_date("0xa", "2026-10-07") is None
        assert db.list_accruals_for_date("2026-10-07") is None
