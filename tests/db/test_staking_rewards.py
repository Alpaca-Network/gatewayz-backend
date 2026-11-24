"""Tests for src.db.staking_rewards (accrual rows behind the emission staker leg)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.staking_rewards import (
    create_accrual,
    get_accrual,
    mark_accrual_paid,
    mark_accrual_pending_failed,
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
            # Chutes-style WAYZ emission rewards (gatewayz-backend
            # tokenomics) -- defaults to the pre-existing rate-table path
            # unless a caller passes source='emission'.
            "source": "rate_table",
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
