"""Tests for src.db.holdings (gatewayz-backend holdings rewards).

Mirrors tests/db/test_staking_rewards.py's MagicMock-chained-query stub --
holdings rewards reuses the staking payout half wholesale and only swaps
the "how much do you have" input, so the DB layer's tests look the same.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.db.holdings import (
    create_holdings_accrual,
    get_active_holdings_rates,
    get_holdings_accrual,
    get_min_usd_for_date,
    list_enabled_tokens,
    list_pending_holdings_accruals,
    list_pending_holdings_accruals_since,
    list_wallets_with_snapshots_for_date,
    mark_holdings_accrual_paid,
    record_snapshot,
    select_holdings_rate,
)


@pytest.fixture
def sb():
    """Opts these tests out of conftest's skip_if_no_database autouse
    fixture (same convention as tests/db/test_staking_rewards.py) -- they
    fully mock the Supabase client and need no real DB."""
    return None


def _mock_table_client(table_data: dict):
    """table_data maps table name -> the .data a chained query call returns.

    Caches one query mock per table name so a later `client.table(name)`
    call in a test's assertions returns the SAME mock the function under
    test used.
    """
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.neq.return_value = query
            query.gte.return_value = query
            query.lt.return_value = query
            query.order.return_value = query
            query.limit.return_value = query
            query.insert.return_value = query
            query.update.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def _boom_client():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    return client


class TestListEnabledTokens:
    def test_returns_only_enabled_rows(self, sb):
        rows = [{"id": 1, "symbol": "WETH", "is_enabled": True}]
        client = _mock_table_client({"holdings_tokens": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert list_enabled_tokens() == rows
        table_query = client.table("holdings_tokens")
        assert table_query.eq.call_args.args == ("is_enabled", True)

    def test_returns_empty_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert list_enabled_tokens() == []


class TestRecordSnapshot:
    def test_inserts_expected_row_and_lowercases_address(self, sb):
        created = {"id": 7, "wallet_address": "0xabc"}
        client = _mock_table_client({"wallet_holdings_snapshots": [created]})
        taken_at = datetime(2026, 9, 14, 0, 20, tzinfo=UTC)
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            result = record_snapshot(
                "0xABC",
                token_id=3,
                raw_amount=1230000000000000000,
                usd_value=Decimal("4210.5"),
                taken_at=taken_at,
            )
        table_query = client.table("wallet_holdings_snapshots")
        args, _ = table_query.insert.call_args
        assert args[0] == {
            "wallet_address": "0xabc",
            "token_id": 3,
            # numeric(78,0)/numeric(38,18) go over the wire as strings so a
            # big int or a Decimal never round-trips through a float.
            "raw_amount": "1230000000000000000",
            "usd_value": "4210.5",
            "taken_at": taken_at.isoformat(),
        }
        assert result == created

    def test_returns_none_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert (
                record_snapshot(
                    "0xabc",
                    token_id=1,
                    raw_amount=1,
                    usd_value=Decimal("1"),
                    taken_at=datetime(2026, 9, 14, tzinfo=UTC),
                )
                is None
            )


class TestGetMinUsdForDate:
    def test_sums_each_batch_then_takes_the_minimum(self, sb):
        # Two snapshot batches for the day: the 00:20 batch totals 150, the
        # 12:20 batch totals 90. The wallet is paid on what it held at its
        # thinnest point, so 90 wins -- never the 150 and never a sum of all
        # six rows.
        rows = [
            {"taken_at": "2026-09-14T00:20:00+00:00", "usd_value": "100"},
            {"taken_at": "2026-09-14T00:20:00+00:00", "usd_value": "50"},
            {"taken_at": "2026-09-14T12:20:00+00:00", "usd_value": "40"},
            {"taken_at": "2026-09-14T12:20:00+00:00", "usd_value": "50"},
        ]
        client = _mock_table_client({"wallet_holdings_snapshots": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            result = get_min_usd_for_date("0xABC", date(2026, 9, 14))
        assert result == Decimal("90")

        table_query = client.table("wallet_holdings_snapshots")
        assert table_query.eq.call_args.args == ("wallet_address", "0xabc")
        # Bounded to the UTC day, half-open so a midnight snapshot belongs
        # to exactly one day.
        assert table_query.gte.call_args.args == ("taken_at", "2026-09-14T00:00:00+00:00")
        assert table_query.lt.call_args.args == ("taken_at", "2026-09-15T00:00:00+00:00")

    def test_single_batch_returns_that_batch_total(self, sb):
        rows = [
            {"taken_at": "2026-09-14T00:20:00+00:00", "usd_value": "12.5"},
            {"taken_at": "2026-09-14T00:20:00+00:00", "usd_value": "0.25"},
        ]
        client = _mock_table_client({"wallet_holdings_snapshots": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_min_usd_for_date("0xabc", date(2026, 9, 14)) == Decimal("12.75")

    def test_returns_none_when_no_snapshots_that_day(self, sb):
        client = _mock_table_client({"wallet_holdings_snapshots": []})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_min_usd_for_date("0xabc", date(2026, 9, 14)) is None

    def test_zero_balance_batch_is_zero_not_none(self, sb):
        # A wallet that emptied itself mid-day has a real 0 minimum -- that
        # must not read as "no data," which would let the job fall through
        # to some other basis.
        rows = [
            {"taken_at": "2026-09-14T00:20:00+00:00", "usd_value": "100"},
            {"taken_at": "2026-09-14T12:20:00+00:00", "usd_value": "0"},
        ]
        client = _mock_table_client({"wallet_holdings_snapshots": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_min_usd_for_date("0xabc", date(2026, 9, 14)) == Decimal("0")

    def test_returns_none_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert get_min_usd_for_date("0xabc", date(2026, 9, 14)) is None


class TestListWalletsWithSnapshotsForDate:
    def test_dedupes_and_lowercases(self, sb):
        rows = [
            {"wallet_address": "0xBBB"},
            {"wallet_address": "0xaaa"},
            {"wallet_address": "0xbbb"},
        ]
        client = _mock_table_client({"wallet_holdings_snapshots": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert list_wallets_with_snapshots_for_date(date(2026, 9, 14)) == ["0xaaa", "0xbbb"]
        table_query = client.table("wallet_holdings_snapshots")
        assert table_query.gte.call_args.args == ("taken_at", "2026-09-14T00:00:00+00:00")
        assert table_query.lt.call_args.args == ("taken_at", "2026-09-15T00:00:00+00:00")

    def test_returns_empty_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert list_wallets_with_snapshots_for_date(date(2026, 9, 14)) == []


class TestGetActiveHoldingsRates:
    def test_filters_active_and_orders_by_min_usd(self, sb):
        rows = [{"id": 1, "min_usd": "0"}, {"id": 2, "min_usd": "1000"}]
        client = _mock_table_client({"holdings_reward_rates": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_active_holdings_rates() == rows
        table_query = client.table("holdings_reward_rates")
        assert table_query.eq.call_args.args == ("is_active", True)
        assert table_query.order.call_args.args == ("min_usd",)

    def test_returns_empty_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert get_active_holdings_rates() == []


class TestSelectHoldingsRate:
    RATES = [
        {"id": 1, "min_usd": "0", "credits_per_1k_usd_per_day": "0.010"},
        {"id": 2, "min_usd": "1000", "credits_per_1k_usd_per_day": "0.012"},
        {"id": 3, "min_usd": "10000", "credits_per_1k_usd_per_day": "0.015"},
    ]

    @pytest.mark.parametrize(
        "usd,expected_id",
        [
            ("0", 1),
            ("999.999999", 1),
            # Exactly on a tier floor takes the higher tier -- the rule is
            # largest min_usd <= value, same as staking's _select_rate.
            ("1000", 2),
            ("1000.000001", 2),
            ("9999.99", 2),
            ("10000", 3),
            ("1000000", 3),
        ],
    )
    def test_picks_largest_floor_at_or_below_value(self, sb, usd, expected_id):
        rate = select_holdings_rate(self.RATES, Decimal(usd))
        assert rate is not None
        assert rate["id"] == expected_id

    def test_returns_none_when_no_tier_covers_value(self, sb):
        # Only reachable if the rates table is misconfigured without its
        # mandatory min_usd = 0 tier.
        assert select_holdings_rate([{"id": 9, "min_usd": "500"}], Decimal("10")) is None

    def test_returns_none_for_empty_rate_table(self, sb):
        assert select_holdings_rate([], Decimal("10")) is None


class TestGetHoldingsAccrual:
    def test_returns_row_and_lowercases_address(self, sb):
        row = {"id": 1, "wallet_address": "0xabc", "reward_date": "2026-09-14"}
        client = _mock_table_client({"holdings_reward_accruals": [row]})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_holdings_accrual("0xABC", date(2026, 9, 14)) == row
        table_query = client.table("holdings_reward_accruals")
        assert table_query.eq.call_args_list[0].args == ("wallet_address", "0xabc")
        assert table_query.eq.call_args_list[1].args == ("reward_date", "2026-09-14")

    def test_returns_none_when_no_row(self, sb):
        client = _mock_table_client({"holdings_reward_accruals": []})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert get_holdings_accrual("0xabc", date(2026, 9, 14)) is None

    def test_returns_none_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert get_holdings_accrual("0xabc", date(2026, 9, 14)) is None


class TestCreateHoldingsAccrual:
    def test_always_inserts_pending_with_no_ledger_request_id(self, sb):
        created = {"id": 1, "status": "pending"}
        client = _mock_table_client({"holdings_reward_accruals": [created]})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            result = create_holdings_accrual(
                "0xABC",
                date(2026, 9, 14),
                usd_basis=Decimal("4210.5"),
                credits=Decimal("0.042105"),
            )
        table_query = client.table("holdings_reward_accruals")
        args, _ = table_query.insert.call_args
        # The row exists as 'pending' BEFORE any credit is written -- the
        # unique (wallet_address, reward_date) index plus this ordering is
        # what makes the payout job non-double-paying, exactly as in
        # staking_reward_accruals.
        assert args[0] == {
            "wallet_address": "0xabc",
            "reward_date": "2026-09-14",
            "usd_basis": "4210.5",
            "credits": "0.042105",
            "status": "pending",
            "ledger_request_id": None,
        }
        assert result == created

    def test_returns_none_on_conflict_or_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert (
                create_holdings_accrual(
                    "0xabc",
                    date(2026, 9, 14),
                    usd_basis=Decimal("1"),
                    credits=Decimal("0"),
                )
                is None
            )


class TestMarkHoldingsAccrualPaid:
    def test_sets_paid_and_stamps_ledger_request_id(self, sb):
        updated = {"id": 1, "status": "paid"}
        client = _mock_table_client({"holdings_reward_accruals": [updated]})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            result = mark_holdings_accrual_paid(1, "holdings_reward:0xabc:2026-09-14")
        table_query = client.table("holdings_reward_accruals")
        args, _ = table_query.update.call_args
        assert args[0]["status"] == "paid"
        assert args[0]["ledger_request_id"] == "holdings_reward:0xabc:2026-09-14"
        assert "updated_at" in args[0]
        assert table_query.eq.call_args.args == ("id", 1)
        assert result == updated

    def test_returns_none_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert mark_holdings_accrual_paid(1, "req") is None


class TestListPendingHoldingsAccruals:
    def test_filters_by_wallet_and_pending_status(self, sb):
        rows = [{"id": 1, "status": "pending"}]
        client = _mock_table_client({"holdings_reward_accruals": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert list_pending_holdings_accruals("0xABC") == rows
        table_query = client.table("holdings_reward_accruals")
        eq_args = [c.args for c in table_query.eq.call_args_list]
        assert ("wallet_address", "0xabc") in eq_args
        assert ("status", "pending") in eq_args

    def test_returns_empty_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert list_pending_holdings_accruals("0xabc") == []


class TestListPendingHoldingsAccrualsSince:
    def test_returns_pending_rows_on_or_after_the_floor(self, sb):
        rows = [{"id": 3, "wallet_address": "0xabc", "reward_date": "2026-09-14"}]
        client = _mock_table_client({"holdings_reward_accruals": rows})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert list_pending_holdings_accruals_since(date(2026, 8, 15)) == rows
        query = client.table("holdings_reward_accruals")
        assert query.eq.call_args.args == ("status", "pending")
        assert query.gte.call_args.args == ("reward_date", "2026-08-15")

    def test_accepts_a_date_string(self, sb):
        client = _mock_table_client({"holdings_reward_accruals": []})
        with patch("src.db.holdings.get_supabase_client", return_value=client):
            assert list_pending_holdings_accruals_since("2026-08-15") == []

    def test_returns_empty_on_error(self, sb):
        with patch("src.db.holdings.get_supabase_client", return_value=_boom_client()):
            assert list_pending_holdings_accruals_since(date(2026, 8, 15)) == []
