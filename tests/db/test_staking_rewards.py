"""Tests for src.db.staking_rewards (gatewayz-backend staking rewards)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.staking_rewards import (
    create_accrual,
    get_accrual,
    get_accruals_for_user,
    get_active_rates,
    get_all_accruals_since,
    list_pending_accruals,
    mark_accrual_paid,
    mark_accrual_pending_failed,
    mark_accrual_skipped,
    replace_active_rates,
)


@pytest.fixture
def sb():
    """Opts these tests out of conftest's skip_if_no_database autouse
    fixture (same convention as tests/db/test_wallet_stakes.py) -- they
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


class TestGetActiveRates:
    def test_returns_rows_ordered_by_min_stake(self, sb):
        rows = [{"id": 1, "min_stake_wayz": "0"}, {"id": 2, "min_stake_wayz": "10000"}]
        client = _mock_table_client({"staking_reward_rates": rows})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = get_active_rates()
        assert result == rows
        table_query = client.table("staking_reward_rates")
        args, _ = table_query.eq.call_args
        assert args == ("active", True)

    def test_returns_empty_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_active_rates() == []


class TestReplaceActiveRates:
    def test_deactivates_then_inserts_new_rows(self, sb):
        new_rows = [{"id": 5, "min_stake_wayz": "0", "credits_per_1k_wayz_per_day": "0.02"}]
        client = _mock_table_client({"staking_reward_rates": new_rows})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = replace_active_rates(
                [{"min_stake_wayz": 0, "credits_per_1k_wayz_per_day": 0.02, "note": "x"}]
            )

        table_query = client.table("staking_reward_rates")
        # update({"active": False}).eq("active", True) happened
        table_query.update.assert_called_once_with({"active": False})
        table_query.insert.assert_called_once()
        assert result == new_rows

    def test_returns_none_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert (
                replace_active_rates([{"min_stake_wayz": 0, "credits_per_1k_wayz_per_day": 0.01}])
                is None
            )


class TestGetAccrual:
    def test_returns_none_when_no_row(self, sb):
        client = _mock_table_client({"staking_reward_accruals": []})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_accrual("0xabc", "2026-09-10") is None

    def test_returns_row_and_lowercases_address(self, sb):
        row = {"wallet_address": "0xabc", "reward_date": "2026-09-10", "status": "paid"}
        client = _mock_table_client({"staking_reward_accruals": [row]})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = get_accrual("0xABC", "2026-09-10")
        assert result == row
        table_query = client.table("staking_reward_accruals")
        assert table_query.eq.call_args_list[0].args == ("wallet_address", "0xabc")

    def test_returns_none_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_accrual("0xabc", "2026-09-10") is None


class TestCreateAccrual:
    def test_inserts_expected_row_and_returns_it(self, sb):
        created = {"id": 1, "wallet_address": "0xabc", "status": "pending"}
        client = _mock_table_client({"staking_reward_accruals": [created]})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = create_accrual(
                "0xABC",
                "2026-09-10",
                "1000000000000000000",
                1,
                "0.010000",
                status="pending",
                user_id=42,
            )
        table_query = client.table("staking_reward_accruals")
        args, _ = table_query.insert.call_args
        assert args[0] == {
            "wallet_address": "0xabc",
            "user_id": 42,
            "reward_date": "2026-09-10",
            "staked_amount_wei": "1000000000000000000",
            "rate_id": 1,
            "credits": "0.010000",
            "status": "pending",
            "skip_reason": None,
        }
        assert result == created

    def test_returns_none_on_conflict_or_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("unique violation")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = create_accrual("0xabc", "2026-09-10", "1", 1, "0.01", status="pending")
        assert result is None


class TestMarkAccrualPaid:
    def test_updates_expected_fields(self, sb):
        client = _mock_table_client({})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = mark_accrual_paid(1, 42, 999, "2026-09-10T00:20:00+00:00")
        assert result is True
        table_query = client.table("staking_reward_accruals")
        args, _ = table_query.update.call_args
        assert args[0] == {
            "status": "paid",
            "user_id": 42,
            "credit_transaction_id": 999,
            "paid_at": "2026-09-10T00:20:00+00:00",
            "skip_reason": None,
        }

    def test_returns_false_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert mark_accrual_paid(1, 42, None, "2026-09-10T00:20:00+00:00") is False


class TestMarkAccrualPendingFailed:
    def test_returns_true_on_success(self, sb):
        client = _mock_table_client({})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert mark_accrual_pending_failed(1, "ValueError") is True

    def test_returns_false_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert mark_accrual_pending_failed(1, "ValueError") is False


class TestMarkAccrualSkipped:
    def test_returns_true_on_success(self, sb):
        client = _mock_table_client({})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert mark_accrual_skipped(1, "below_min") is True

    def test_returns_false_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert mark_accrual_skipped(1, "below_min") is False


class TestListPendingAccruals:
    def test_filters_by_status_and_date(self, sb):
        rows = [{"id": 1, "status": "pending"}]
        client = _mock_table_client({"staking_reward_accruals": rows})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            result = list_pending_accruals("2026-08-11")
        assert result == rows
        table_query = client.table("staking_reward_accruals")
        assert table_query.eq.call_args_list[0].args == ("status", "pending")
        assert table_query.gte.call_args.args == ("reward_date", "2026-08-11")

    def test_scopes_to_one_wallet_when_given(self, sb):
        client = _mock_table_client({"staking_reward_accruals": []})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            list_pending_accruals("2026-08-11", wallet_address="0xABC")
        table_query = client.table("staking_reward_accruals")
        assert ("wallet_address", "0xabc") in [c.args for c in table_query.eq.call_args_list]

    def test_returns_empty_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert list_pending_accruals("2026-08-11") == []


class TestGetAccrualsForUser:
    def test_returns_rows(self, sb):
        rows = [{"id": 1, "user_id": 42}]
        client = _mock_table_client({"staking_reward_accruals": rows})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_accruals_for_user(42) == rows

    def test_returns_empty_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_accruals_for_user(42) == []


class TestGetAllAccrualsSince:
    def test_returns_rows_without_date_filter(self, sb):
        rows = [{"id": 1}]
        client = _mock_table_client({"staking_reward_accruals": rows})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_all_accruals_since() == rows
        table_query = client.table("staking_reward_accruals")
        table_query.gte.assert_not_called()

    def test_applies_date_filter_when_given(self, sb):
        client = _mock_table_client({"staking_reward_accruals": []})
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            get_all_accruals_since("2026-08-11")
        table_query = client.table("staking_reward_accruals")
        table_query.gte.assert_called_once_with("reward_date", "2026-08-11")

    def test_returns_empty_on_error(self, sb):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch("src.db.staking_rewards.get_supabase_client", return_value=client):
            assert get_all_accruals_since() == []
