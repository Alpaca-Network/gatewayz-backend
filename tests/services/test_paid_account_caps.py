"""Token caps for paying accounts.

Every token is billed from credits, so for an account with money in it the
token caps only stop runaway use. The 500k-a-day default cut an agent session
off after about 25 requests.
"""

from unittest.mock import MagicMock, patch

import pytest

import src.db.plans as plans

USAGE = {
    "limits": {
        "daily_request_limit": 25000,
        "monthly_request_limit": 750000,
        "daily_token_limit": 500_000,
        "monthly_token_limit": 15_000_000,
    },
    "usage": {
        "daily_requests": 10,
        "monthly_requests": 10,
        "daily_tokens": 600_000,
        "monthly_tokens": 600_000,
    },
}


@pytest.fixture(autouse=True)
def _clear_cache():
    plans._paid_account_cache.clear()
    yield
    plans._paid_account_cache.clear()


def _enforce(paid: bool, env: str = "live", usage=USAGE):
    with (
        patch.object(plans, "is_admin_tier_user", return_value=False),
        patch.object(plans, "get_user_usage_within_plan_limits", return_value=usage),
        patch.object(plans, "is_paid_account", return_value=paid),
    ):
        return plans.enforce_plan_limits(7, 20_000, env)


def test_unpaid_account_keeps_the_default_daily_cap():
    result = _enforce(paid=False)
    assert result["allowed"] is False
    assert "Daily token limit exceeded (500000)" in result["reason"]


def test_paid_account_gets_the_agent_caps():
    assert _enforce(paid=True) == {"allowed": True, "reason": "Within plan limits"}


def test_paid_account_still_stops_at_the_higher_cap():
    usage = {**USAGE, "usage": {**USAGE["usage"], "daily_tokens": 19_990_000}}
    result = _enforce(paid=True, usage=usage)
    assert result["allowed"] is False
    assert "(20000000)" in result["reason"]


def test_test_keys_get_half():
    usage = {**USAGE, "usage": {**USAGE["usage"], "daily_tokens": 10_000_000}}
    result = _enforce(paid=True, env="test", usage=usage)
    assert result["allowed"] is False
    assert "(10000000)" in result["reason"]


def test_a_plan_above_the_floor_is_not_lowered():
    usage = {
        **USAGE,
        "limits": {
            **USAGE["limits"],
            "daily_token_limit": 100_000_000,
            "monthly_token_limit": 2_000_000_000,
        },
        "usage": {**USAGE["usage"], "daily_tokens": 50_000_000, "monthly_tokens": 500_000_000},
    }
    assert _enforce(paid=True, usage=usage)["allowed"] is True


def _client(user_row, plan_rows):
    client = MagicMock()

    def table(name):
        t = MagicMock()
        chain = t.select.return_value.eq.return_value
        if name == "users":
            chain.limit.return_value.execute.return_value.data = [user_row]
        else:
            chain.eq.return_value.execute.return_value.data = plan_rows
        return t

    client.table.side_effect = table
    return client


@pytest.mark.parametrize(
    "user_row, plan_rows, expected",
    [
        ({"purchased_credits": 14.06, "subscription_allowance": 0}, [], True),
        ({"purchased_credits": 0, "subscription_allowance": 5}, [], True),
        (
            {"purchased_credits": 0, "subscription_allowance": 0},
            [{"plans": {"price_per_month": "35.00"}}],
            True,
        ),
        (
            {"purchased_credits": 0, "subscription_allowance": 0},
            [{"plans": {"price_per_month": "0.00"}}],
            False,
        ),
        ({"purchased_credits": None, "subscription_allowance": None}, [], False),
    ],
)
def test_is_paid_account(user_row, plan_rows, expected):
    with patch.object(plans, "get_supabase_client", return_value=_client(user_row, plan_rows)):
        assert plans.is_paid_account(7) is expected


def test_database_error_means_default_caps_and_is_not_cached():
    broken = MagicMock()
    broken.table.side_effect = RuntimeError("db down")
    with patch.object(plans, "get_supabase_client", return_value=broken):
        assert plans.is_paid_account(7) is False
    assert 7 not in plans._paid_account_cache


def test_plan_change_clears_the_cached_answer():
    plans._paid_account_cache[7] = (False, 0.0)
    plans.invalidate_user_plan_cache(7)
    assert 7 not in plans._paid_account_cache
