"""Regression test for the authenticated non-streaming double-charge bug.

The unified ChatInferenceHandler deducts credits + records usage for authenticated
non-streaming requests. The route then called handle_credits_and_usage(), which
deducted AGAIN under a different request_id (the idempotency guard only collapses
matching request_ids), charging paid users twice per request.

The fix: the route passes already_charged=True, so handle_credits_and_usage skips
the duplicate deduction + usage record but still performs the route-owned
bookkeeping (rate-limit usage update).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.billing.credit_handler import handle_credits_and_usage


@pytest.fixture
def _patch_billing_deps(monkeypatch):
    """Patch the functions handle_credits_and_usage imports at call time.

    handle_credits_and_usage does function-local imports, so we patch the
    attributes on the source module objects (object form of setattr) — that is
    what the ``from X import Y`` inside the function resolves against.
    """
    import src.db.rate_limits as rate_limits_mod
    import src.db.trials as trials_mod
    import src.db.users as users_mod
    import src.services.pricing as pricing_mod

    deduct = MagicMock()
    record = MagicMock()
    rate_limit = MagicMock()

    monkeypatch.setattr(users_mod, "deduct_credits", deduct)
    monkeypatch.setattr(users_mod, "record_usage", record)
    monkeypatch.setattr(users_mod, "log_api_usage_transaction", MagicMock())
    monkeypatch.setattr(rate_limits_mod, "update_rate_limit_usage", rate_limit)
    monkeypatch.setattr(trials_mod, "track_trial_usage_for_key", MagicMock())
    monkeypatch.setattr(pricing_mod, "calculate_cost_async", AsyncMock(return_value=0.02))
    return {"deduct": deduct, "record": record, "rate_limit": rate_limit}


_PAID_USER = {"id": 123, "tier": "pro", "subscription_allowance": 0, "purchased_credits": 100}
_NOT_TRIAL = {"is_trial": False, "is_expired": False}


@pytest.mark.asyncio
async def test_already_charged_skips_deduction(_patch_billing_deps):
    """already_charged=True must NOT deduct (handler already did) but MUST still
    update rate-limit usage."""
    cost = await handle_credits_and_usage(
        api_key="gw_test",
        user=_PAID_USER,
        model="openai/gpt-4o",
        trial=_NOT_TRIAL,
        total_tokens=100,
        prompt_tokens=60,
        completion_tokens=40,
        elapsed_ms=250,
        request_id="route-uuid-1",
        already_charged=True,
    )

    assert cost == 0.02
    _patch_billing_deps["deduct"].assert_not_called()  # no double charge
    _patch_billing_deps["record"].assert_not_called()  # no double usage record
    _patch_billing_deps["rate_limit"].assert_called_once()  # route bookkeeping preserved


@pytest.mark.asyncio
async def test_not_already_charged_still_deducts(_patch_billing_deps):
    """Default path (already_charged=False) must deduct exactly once — the fix
    must not break endpoints where the handler did not charge."""
    await handle_credits_and_usage(
        api_key="gw_test",
        user=_PAID_USER,
        model="openai/gpt-4o",
        trial=_NOT_TRIAL,
        total_tokens=100,
        prompt_tokens=60,
        completion_tokens=40,
        elapsed_ms=250,
        request_id="route-uuid-2",
        already_charged=False,
    )

    _patch_billing_deps["deduct"].assert_called_once()
    _patch_billing_deps["rate_limit"].assert_called_once()
