"""Regression tests for the security/scalability hardening pass.

Covers the fixes applied after the "vibe-coded app" audit:
  1. Per-API-key rate-limit guard for expensive endpoints (images/audio/payments)
  2. Payment IDOR ownership checks (checkout session / payment intent retrieval)
  3. Stripe webhook idempotency (no double-credit on retry; record-after-success)
  4. Configurable Redis fail-open vs fail-closed behaviour
"""

import inspect
import types

import pytest
from fastapi import HTTPException

# --------------------------------------------------------------------------- #
# 1. Per-API-key rate-limit guard                                             #
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, allowed, reason=None, retry_after=None):
        self.allowed = allowed
        self.reason = reason
        self.retry_after = retry_after


def _no_call():
    raise AssertionError("rate limit manager should not be consulted")


async def test_guard_bypassed_when_disabled(monkeypatch):
    from src.utils import rate_limit_guard

    monkeypatch.setenv("DISABLE_RATE_LIMITING", "true")
    monkeypatch.setattr(rate_limit_guard, "get_rate_limit_manager", _no_call)
    assert await rate_limit_guard.enforce_request_rate_limit("gw_live_key") is None


async def test_guard_noop_without_api_key(monkeypatch):
    from src.utils import rate_limit_guard

    monkeypatch.delenv("DISABLE_RATE_LIMITING", raising=False)
    monkeypatch.setattr(rate_limit_guard, "get_rate_limit_manager", _no_call)
    assert await rate_limit_guard.enforce_request_rate_limit("") is None
    assert await rate_limit_guard.enforce_request_rate_limit(None) is None


async def test_guard_allows_under_limit(monkeypatch):
    from src.utils import rate_limit_guard

    monkeypatch.delenv("DISABLE_RATE_LIMITING", raising=False)

    class _Mgr:
        async def check_rate_limit(self, api_key, tokens_used=0):
            return _Result(allowed=True)

    monkeypatch.setattr(rate_limit_guard, "get_rate_limit_manager", lambda: _Mgr())
    result = await rate_limit_guard.enforce_request_rate_limit("gw_live_key")
    assert result is not None and result.allowed is True


async def test_guard_raises_429_over_limit(monkeypatch):
    from src.utils import rate_limit_guard

    monkeypatch.delenv("DISABLE_RATE_LIMITING", raising=False)

    class _Mgr:
        async def check_rate_limit(self, api_key, tokens_used=0):
            return _Result(allowed=False, reason="Rate limit exceeded", retry_after=42)

    monkeypatch.setattr(rate_limit_guard, "get_rate_limit_manager", lambda: _Mgr())
    with pytest.raises(HTTPException) as exc:
        await rate_limit_guard.enforce_request_rate_limit("gw_live_key")
    assert exc.value.status_code == 429
    assert exc.value.headers.get("Retry-After") == "42"


async def test_guard_fails_open_when_manager_missing(monkeypatch):
    from src.utils import rate_limit_guard

    monkeypatch.delenv("DISABLE_RATE_LIMITING", raising=False)
    monkeypatch.setattr(rate_limit_guard, "get_rate_limit_manager", lambda: None)
    assert await rate_limit_guard.enforce_request_rate_limit("gw_live_key") is None


def test_expensive_endpoints_invoke_guard():
    """images/audio/payments routes must actually call the guard."""
    import src.routes.audio as audio
    import src.routes.images as images
    import src.routes.payments as payments

    assert "enforce_request_rate_limit" in inspect.getsource(images.generate_images)
    assert "enforce_request_rate_limit" in inspect.getsource(audio.create_transcription)
    assert "enforce_request_rate_limit" in inspect.getsource(payments.create_checkout_session)
    assert "enforce_request_rate_limit" in inspect.getsource(payments.create_payment_intent)


# --------------------------------------------------------------------------- #
# 2. Payment IDOR ownership checks                                            #
# --------------------------------------------------------------------------- #


def test_owner_can_access_stripe_object():
    from src.routes.payments import _assert_stripe_object_owner

    # Matching owner (string vs int coercion) must not raise.
    _assert_stripe_object_owner(
        {"user_id": "7"}, {"id": 7}, object_label="Checkout session", object_id="cs_1"
    )


def test_non_owner_blocked_with_404():
    from src.routes.payments import _assert_stripe_object_owner

    with pytest.raises(HTTPException) as exc:
        _assert_stripe_object_owner(
            {"user_id": "8"}, {"id": 7}, object_label="Checkout session", object_id="cs_1"
        )
    assert exc.value.status_code == 404


def test_missing_metadata_blocked_with_404():
    from src.routes.payments import _assert_stripe_object_owner

    with pytest.raises(HTTPException) as exc:
        _assert_stripe_object_owner(
            None, {"id": 7}, object_label="Payment intent", object_id="pi_1"
        )
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# 3. Stripe webhook idempotency                                               #
# --------------------------------------------------------------------------- #


def test_payment_succeeded_skips_duplicate_credit(monkeypatch):
    from src.services.billing import payments as pay

    svc = pay.StripeService.__new__(pay.StripeService)
    calls = {"add": 0, "update": 0}
    monkeypatch.setattr(
        pay,
        "get_payment_by_stripe_intent",
        lambda pid: {"id": 10, "user_id": 1, "status": "completed", "amount": 5},
    )
    monkeypatch.setattr(
        pay, "update_payment_status", lambda **k: calls.__setitem__("update", calls["update"] + 1)
    )
    monkeypatch.setattr(
        pay, "add_credits_to_user", lambda **k: calls.__setitem__("add", calls["add"] + 1)
    )

    svc._handle_payment_succeeded(types.SimpleNamespace(id="pi_1"))

    assert calls["add"] == 0, "must not re-grant credits for an already-completed payment"
    assert calls["update"] == 0, "must short-circuit before re-marking the payment"


def test_payment_succeeded_grants_when_pending(monkeypatch):
    from src.services.billing import payments as pay

    svc = pay.StripeService.__new__(pay.StripeService)
    calls = {"add": 0}
    monkeypatch.setattr(
        pay,
        "get_payment_by_stripe_intent",
        lambda pid: {"id": 10, "user_id": 1, "status": "pending", "amount": 5},
    )
    monkeypatch.setattr(pay, "update_payment_status", lambda **k: None)
    monkeypatch.setattr(
        pay, "add_credits_to_user", lambda **k: calls.__setitem__("add", calls["add"] + 1)
    )

    svc._handle_payment_succeeded(types.SimpleNamespace(id="pi_1"))

    assert calls["add"] == 1, "a pending payment should be granted exactly once"


def test_webhook_records_event_after_handlers():
    """A failed handler must release its claim AFTER dispatch, so Stripe retries.

    Dedup switched from a check-then-record pair to insert-first idempotency:
    the event is claimed up front (claim_event) before handler dispatch, and on
    handler failure the claim is released (release_event) so the event is retried
    instead of being silently marked done. This test enforces that ordering.
    """
    from src.services.billing.payments import StripeService

    src = inspect.getsource(StripeService.handle_webhook)
    claim_idx = src.index("claim_event(")
    handler_idx = src.index("_handle_checkout_completed")
    release_idx = src.rindex("release_event(")
    # Event is claimed before the handler runs (insert-first idempotency)...
    assert claim_idx < handler_idx, "claim_event must run before handler dispatch"
    # ...and only released after a handler fails, so Stripe retries instead of the
    # event being silently dropped.
    assert release_idx > handler_idx, (
        "release_event must be called after the handler dispatch so a failed "
        "handler is retried by Stripe instead of being silently dropped"
    )


def test_checkout_handler_is_idempotent():
    from src.services.billing.payments import StripeService

    src = inspect.getsource(StripeService._handle_checkout_completed)
    assert "get_payment(" in src and "already completed" in src


# --------------------------------------------------------------------------- #
# 4. Configurable Redis fail-open vs fail-closed                              #
# --------------------------------------------------------------------------- #


def test_sliding_window_fails_open_by_default(monkeypatch):
    import src.config.redis_config as rc
    from src.services.rate_limiting import sliding_window_check

    monkeypatch.delenv("RATE_LIMIT_FAIL_CLOSED", raising=False)
    monkeypatch.setattr(rc, "get_redis_client", lambda: None)

    allowed, _remaining, _retry = sliding_window_check("k", 100, 60)
    assert allowed is True


def test_sliding_window_fails_closed_when_configured(monkeypatch):
    import src.config.redis_config as rc
    from src.services.rate_limiting import sliding_window_check

    monkeypatch.setenv("RATE_LIMIT_FAIL_CLOSED", "true")
    monkeypatch.setattr(rc, "get_redis_client", lambda: None)

    allowed, remaining, _retry = sliding_window_check("k", 100, 60)
    assert allowed is False
    assert remaining == 0
