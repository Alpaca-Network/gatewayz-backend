"""Ledger precision + daily-limit correctness.

1. credit_transactions.amount was numeric(10,2): sub-cent charges stored as 0.00.
2. deduct_credits skipped < 1e-6 and users balances were DECIMAL(10,4): tiny
   requests were free. Sub-precision costs must round UP to the ledger unit.
3. get_daily_usage summed a row-capped select and failed open on error.
4. A cheap pre-inference check must exist.
"""

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

MIG_DIR = Path(__file__).resolve().parents[2] / "supabase/migrations"


def _migration() -> str:
    files = sorted(MIG_DIR.glob("*ledger_precision*.sql"))
    assert files, "ledger precision migration missing"
    return files[-1].read_text()


class TestMigration:
    def test_widens_ledger_and_balance_columns(self):
        sql = _migration().lower()
        for col in ("amount", "balance_before", "balance_after"):
            assert re.search(rf"alter column {col}\s+type numeric\(14,\s*8\)", sql), col
        for col in ("subscription_allowance", "purchased_credits"):
            assert re.search(rf"alter column {col}\s+type numeric\(14,\s*8\)", sql), col

    def test_adds_daily_usage_aggregate_rpc(self):
        sql = _migration().lower()
        assert "create or replace function public.get_daily_usage_total" in sql
        assert "sum(-amount)" in sql.replace(" ", "") or "sum(-amount)" in sql
        assert "grant execute" in sql and "service_role" in sql


class TestCeilToLedgerUnit:
    def test_sub_precision_rounds_up(self):
        from src.db.users import quantize_cost_up

        assert quantize_cost_up(1e-9) == Decimal("0.00000001")
        assert quantize_cost_up(0.000000001) > 0

    def test_exact_values_unchanged(self):
        from src.db.users import quantize_cost_up

        assert quantize_cost_up(0.00003) == Decimal("0.00003000")
        assert quantize_cost_up(0.1 + 0.2) == Decimal("0.30000001") or quantize_cost_up(
            0.1 + 0.2
        ) == Decimal("0.30000000")

    def test_zero_stays_zero(self):
        from src.db.users import quantize_cost_up

        assert quantize_cost_up(0) == Decimal(0)


def _client_for_deduct(rpc_data):
    client = MagicMock()

    def table(name):
        t = MagicMock()
        res = MagicMock()
        if name == "api_keys_new":
            res.data = [{"user_id": 7}]
        else:
            res.data = [
                {"id": 7, "subscription_allowance": 0, "purchased_credits": 5, "tier": "basic"}
            ]
        t.select.return_value.eq.return_value.execute.return_value = res
        return t

    client.table.side_effect = table
    client.rpc.return_value.execute.return_value.data = rpc_data
    return client


class TestDeductCreditsTinyCost:
    def _run(self, tokens):
        rpc = {
            "success": True,
            "transaction_id": 1,
            "new_allowance": 0,
            "new_purchased": 4,
            "new_balance": 4,
        }
        client = _client_for_deduct(rpc)
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.plans.is_admin_tier_user", return_value=False),
            patch("src.services.daily_usage_limiter.enforce_daily_usage_limit"),
            patch("src.db.users.invalidate_user_cache"),
        ):
            from src.db.users import deduct_credits

            deduct_credits("key", tokens, "t", {})
        return client

    def test_sub_micro_cost_is_charged_not_skipped(self):
        client = self._run(1e-7)
        assert client.rpc.called
        params = client.rpc.call_args[0][1]
        assert params["p_tokens_amount"] > 0
        assert abs(params["p_from_allowance"] + params["p_from_purchased"] - params["p_tokens_amount"]) < 1e-9

    def test_sub_precision_cost_rounds_up_to_ledger_unit(self):
        client = self._run(1e-10)
        assert client.rpc.call_args[0][1]["p_tokens_amount"] == pytest.approx(1e-8)

    def test_exactly_zero_is_still_skipped(self):
        client = self._run(0.0)
        assert not client.rpc.called


class TestDailyUsageAggregate:
    def test_uses_rpc_sum_not_row_capped_select(self):
        from src.services.billing import daily_usage_limiter as d

        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = 0.75
        with patch.object(d, "get_supabase_client", return_value=client):
            assert d.get_daily_usage(1) == pytest.approx(0.75)
        assert client.rpc.call_args[0][0] == "get_daily_usage_total"
        client.table.assert_not_called()

    def test_rpc_missing_falls_back_to_paginated_sum_over_1000_rows(self):
        from src.services.billing import daily_usage_limiter as d

        client = MagicMock()
        client.rpc.return_value.execute.side_effect = Exception("function does not exist")
        pages = [[{"amount": -0.001}] * 1000, [{"amount": -0.001}] * 500, []]
        q = client.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value
        q.order.return_value.range.return_value.execute.side_effect = [
            MagicMock(data=p) for p in pages
        ]
        with patch.object(d, "get_supabase_client", return_value=client):
            assert d.get_daily_usage(1) == pytest.approx(1.5)

    def test_lookup_failure_raises_instead_of_reporting_zero(self):
        from src.services.billing import daily_usage_limiter as d

        client = MagicMock()
        client.rpc.return_value.execute.side_effect = Exception("boom")
        client.table.side_effect = Exception("boom")
        with patch.object(d, "get_supabase_client", return_value=client):
            with pytest.raises(d.DailyUsageUnavailable):
                d.get_daily_usage(1)

    def test_post_inference_enforcement_still_fails_open(self):
        """Never drop a charge for a request already served."""
        from src.services.billing import daily_usage_limiter as d

        with patch.object(d, "get_daily_usage", side_effect=d.DailyUsageUnavailable("x")):
            d.enforce_daily_usage_limit(1, 0.01)  # must not raise


class TestPreflight:
    def test_blocks_when_at_limit(self):
        from src.services.billing import daily_usage_limiter as d

        with (
            patch.object(d, "get_daily_usage", return_value=d.DAILY_USAGE_LIMIT),
            patch("src.db.plans.is_admin_tier_user", return_value=False),
        ):
            with pytest.raises(d.DailyUsageLimitExceeded):
                d.check_daily_limit_preflight(1)

    def test_allows_under_limit(self):
        from src.services.billing import daily_usage_limiter as d

        with (
            patch.object(d, "get_daily_usage", return_value=0.1),
            patch("src.db.plans.is_admin_tier_user", return_value=False),
        ):
            d.check_daily_limit_preflight(1)

    def test_fails_closed_on_lookup_error(self):
        from src.services.billing import daily_usage_limiter as d

        with (
            patch.object(d, "get_daily_usage", side_effect=d.DailyUsageUnavailable("x")),
            patch("src.db.plans.is_admin_tier_user", return_value=False),
        ):
            with pytest.raises(d.DailyUsageUnavailable):
                d.check_daily_limit_preflight(1)

    def test_admin_bypasses(self):
        from src.services.billing import daily_usage_limiter as d

        with (
            patch.object(d, "get_daily_usage", return_value=999),
            patch("src.db.plans.is_admin_tier_user", return_value=True),
        ):
            d.check_daily_limit_preflight(1)
