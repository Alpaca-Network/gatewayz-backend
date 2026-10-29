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
    query.in_.assert_called_with("status", ["pending", "claimed", "paid"])


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


def test_reserve_accrual_calls_the_atomic_rpc(sb):
    client = MagicMock()
    client.rpc.return_value.execute.return_value = MagicMock(
        data={"status": "created", "accrual": {"id": 9}}
    )
    with patch.object(db, "get_supabase_client", return_value=client):
        result = db.reserve_accrual(
            "0xA",
            "ada",
            date(2026, 10, 7),
            Decimal(1),
            Decimal(2),
            Decimal(3),
            5,
            Decimal(5),
            Decimal(50),
        )
    assert result == {"status": "created", "accrual": {"id": 9}}
    name, params = client.rpc.call_args.args
    assert name == "delegation_reserve_accrual"
    assert params["p_wallet_address"] == "0xa" and params["p_user_id"] == 5
    assert params["p_account_cap"] == "5" and params["p_global_budget"] == "50"


def test_rpc_failures_reserve_and_claim_nothing(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken_rpc()):
        assert (
            db.reserve_accrual(
                "0xa",
                "eth",
                date(2026, 10, 7),
                Decimal(1),
                Decimal(1),
                Decimal(1),
                None,
                Decimal(5),
                Decimal(50),
            )
            is None
        )
        assert db.claim_accrual(1, 2, Decimal(5)) is None


def _broken_rpc():
    client = MagicMock()
    client.rpc.side_effect = RuntimeError("function does not exist")
    return client


def test_mark_paid_only_moves_a_claimed_row(sb):
    client, query = _query([[{"id": 1, "status": "paid"}]])
    query.update.return_value = query
    with patch.object(db, "get_supabase_client", return_value=client):
        db.mark_accrual_paid(1, "uuid")
    query.eq.assert_any_call("status", "claimed")


# -- v3 reward-share attributions + the discount ledger -------------------------------


def test_measurement_amounts_are_none_on_error_or_garbage(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.get_measurement_amounts_for_date("eth", date(2026, 10, 9)) is None
    client, _ = _query([[{"wallet_address": "0xA", "amount_raw": "nan"}]])
    with patch.object(db, "get_supabase_client", return_value=client):
        assert db.get_measurement_amounts_for_date("eth", date(2026, 10, 9)) is None
    client, _ = _query(
        [
            [
                {"wallet_address": "0xA", "amount_raw": "5"},
                {"wallet_address": "0xa", "amount_raw": "3"},
            ]
        ]
    )
    with patch.object(db, "get_supabase_client", return_value=client):
        assert db.get_measurement_amounts_for_date("eth", date(2026, 10, 9)) == {"0xa": [5, 3]}


def test_attribution_sums_fail_closed(sb):
    with patch.object(db, "get_supabase_client", return_value=_broken()):
        assert db.sum_attributed_discount("eth") is None
        assert db.list_revenue_since("eth", "2026-10-01") is None
        assert db.list_attribution_user_ids() is None
        assert db.get_latest_sweep_rows("eth") is None
        assert (
            db.reserve_attribution(
                "0xa",
                "eth",
                "k",
                "2026-10-10",
                1,
                Decimal(1),
                Decimal(2),
                Decimal(1),
                Decimal("0.95"),
                "00000000-0000-0000-0000-000000000001",
                Decimal(5),
                Decimal(50),
            )
            is None
        )
        assert db.grant_attribution(1, 1, Decimal(5)) is None


def test_reserve_and_grant_send_strings_for_money(sb):
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = {"status": "created", "attribution": {}}
    with patch.object(db, "get_supabase_client", return_value=client):
        db.reserve_attribution(
            "0xAA",
            "eth",
            "2026-10-10",
            date(2026, 10, 10),
            7,
            Decimal("1.5"),
            Decimal(10),
            Decimal("4.2"),
            Decimal("0.95"),
            "00000000-0000-0000-0000-000000000001",
            Decimal(5),
            Decimal(50),
        )
        name, params = client.rpc.call_args.args
        assert name == "delegation_reserve_attribution"
        assert params["p_wallet_address"] == "0xaa" and params["p_reward_date"] == "2026-10-10"
        assert params["p_attributed_usd"] == "4.2" and params["p_reward_share"] == "0.95"
        db.grant_attribution(3, 7, Decimal(5))
        assert client.rpc.call_args.args == (
            "delegation_grant_attribution",
            {"p_attribution_id": 3, "p_user_id": 7, "p_account_cap": "5"},
        )


def test_insert_revenue_sends_the_denominator_only_when_known(sb):
    client, query = _query([[{"id": 1}], [{"id": 2}]])
    with patch.object(db, "get_supabase_client", return_value=client):
        db.insert_revenue("eth", date(2026, 10, 10), "2026-10-10", Decimal(1), Decimal(2), "s")
        assert "stake_total_native" not in query.insert.call_args.args[0]
        db.insert_revenue(
            "eth",
            date(2026, 10, 11),
            "2026-10-11",
            Decimal(1),
            Decimal(2),
            "s",
            stake_total_native=Decimal("990.5"),
        )
        assert query.insert.call_args.args[0]["stake_total_native"] == "990.5"


class TestInferenceDiscountDB:
    def test_apply_raises_on_rpc_failure_and_garbage(self, sb):
        import src.db.inference_discount as idb

        client = MagicMock()
        client.rpc.side_effect = RuntimeError("https://x.supabase.co?key=secret")
        with patch.object(idb, "get_supabase_client", return_value=client):
            with pytest.raises(idb.DiscountUnavailableError) as exc:
                idb.apply_discount(7, Decimal(1), Decimal("0.99"), "u")
            assert "secret" not in str(exc.value)
        for bad in (None, "x", -1, True, [], {"a": 1, "b": 2}, "inf"):
            client = MagicMock()
            client.rpc.return_value.execute.return_value.data = bad
            with patch.object(idb, "get_supabase_client", return_value=client):
                with pytest.raises(idb.DiscountUnavailableError):
                    idb.apply_discount(7, Decimal(1), Decimal("0.99"), "u")

    @pytest.mark.parametrize("data", [1.98, "1.98", [1.98], [{"discount_apply": 1.98}]])
    def test_apply_parses_scalar_shapes(self, sb, data):
        import src.db.inference_discount as idb

        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = data
        with patch.object(idb, "get_supabase_client", return_value=client):
            assert idb.apply_discount(7, Decimal(2), Decimal("0.99"), "u") == Decimal("1.98")
        params = client.rpc.call_args.args[1]
        assert params["p_cost"] == "2" and params["p_discount_percent"] == "0.99"

    def test_reads_have_safe_defaults(self, sb):
        import src.db.inference_discount as idb

        with patch.object(idb, "get_supabase_client", return_value=_broken()):
            assert idb.get_balance(7) is None
            assert idb.list_ledger(7) == []
            assert idb.adjust_discount(7, Decimal(1), "u", "r", 1) is None
        with patch.object(db, "get_supabase_client", return_value=_broken()):
            assert idb.list_balances() is None
