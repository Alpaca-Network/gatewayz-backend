"""DAILY_USAGE_LIMIT_USD / DAILY_LIMIT_APPLIES_TO policy for the opt-in daily cap."""

import importlib
from unittest.mock import MagicMock, patch

import pytest

from src.services.billing import daily_usage_limiter as d


def _client(row):
    c = MagicMock()
    chain = c.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value.data = [] if row is None else [row]
    return c


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(d, "ENFORCE_DAILY_LIMITS", True)
    monkeypatch.setattr(d, "DAILY_USAGE_LIMIT", 1.0)
    monkeypatch.setattr(d, "DAILY_LIMIT_APPLIES_TO", "free_only")
    with patch("src.db.plans.is_admin_tier_user", return_value=False):
        yield


def _over():
    return patch.object(d, "get_daily_usage", return_value=5.0)


def test_free_only_blocks_free_user(enforced):
    row = {"purchased_credits": 0, "subscription_status": "inactive"}
    with _over(), patch.object(d, "get_supabase_client", return_value=_client(row)):
        with pytest.raises(d.DailyUsageLimitExceeded):
            d.check_daily_limit_preflight(1)


def test_free_only_exempts_purchased_credits(enforced):
    row = {"purchased_credits": 12.5, "subscription_status": "inactive"}
    with _over(), patch.object(d, "get_supabase_client", return_value=_client(row)):
        d.check_daily_limit_preflight(1)
        assert d.check_daily_usage_limit(1, 1.0)["allowed"] is True
        d.enforce_daily_usage_limit(1, 1.0)


def test_free_only_exempts_active_subscription(enforced):
    row = {"purchased_credits": 0, "subscription_status": "active"}
    with _over(), patch.object(d, "get_supabase_client", return_value=_client(row)):
        d.check_daily_limit_preflight(1)
        d.enforce_daily_usage_limit(1, 1.0)


def test_all_caps_paying_users(enforced, monkeypatch):
    monkeypatch.setattr(d, "DAILY_LIMIT_APPLIES_TO", "all")
    row = {"purchased_credits": 100, "subscription_status": "active"}
    with _over(), patch.object(d, "get_supabase_client", return_value=_client(row)):
        with pytest.raises(d.DailyUsageLimitExceeded):
            d.check_daily_limit_preflight(1)
        with pytest.raises(d.DailyUsageLimitExceeded):
            d.enforce_daily_usage_limit(1, 0.1)


def test_enforce_blocks_free_user(enforced):
    row = {"purchased_credits": 0, "subscription_status": None}
    with _over(), patch.object(d, "get_supabase_client", return_value=_client(row)):
        with pytest.raises(d.DailyUsageLimitExceeded):
            d.enforce_daily_usage_limit(1, 0.1)


def test_scope_lookup_failure_preflight_fails_closed_enforce_fails_open(enforced):
    c = MagicMock()
    c.table.side_effect = RuntimeError("db")
    with _over(), patch.object(d, "get_supabase_client", return_value=c):
        with pytest.raises(d.DailyUsageUnavailable):
            d.check_daily_limit_preflight(1)
        d.enforce_daily_usage_limit(1, 0.1)  # served request keeps its charge


def test_default_off_never_reads_anything(monkeypatch):
    monkeypatch.setattr(d, "ENFORCE_DAILY_LIMITS", False)
    with patch.object(d, "get_supabase_client", side_effect=AssertionError("no db")):
        d.check_daily_limit_preflight(1)
        d.enforce_daily_usage_limit(1, 999)


def _reload(monkeypatch, **env):
    import src.config.usage_limits as u

    for k in ("DAILY_USAGE_LIMIT_USD", "DAILY_LIMIT_APPLIES_TO", "ENFORCE_DAILY_LIMITS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(u)


def test_env_parsing(monkeypatch):
    u = _reload(monkeypatch)
    assert (u.DAILY_USAGE_LIMIT, u.DAILY_LIMIT_APPLIES_TO, u.ENFORCE_DAILY_LIMITS) == (
        1.0,
        "free_only",
        False,
    )
    u = _reload(monkeypatch, DAILY_USAGE_LIMIT_USD="2.5", DAILY_LIMIT_APPLIES_TO="ALL")
    assert (u.DAILY_USAGE_LIMIT, u.DAILY_LIMIT_APPLIES_TO) == (2.5, "all")
    for bad in ("abc", "-3", "0"):
        assert _reload(monkeypatch, DAILY_USAGE_LIMIT_USD=bad).DAILY_USAGE_LIMIT == 1.0
    assert (
        _reload(monkeypatch, DAILY_LIMIT_APPLIES_TO="bogus").DAILY_LIMIT_APPLIES_TO == "free_only"
    )
    _reload(monkeypatch)
