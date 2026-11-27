"""Tests for src/services/billing/card_auto_topup.py -- saving a card and
charging it off-session when the paid balance cannot cover a request.

Stripe, the database and email are all mocked: nothing here reaches Stripe
(test mode or otherwise) and no charge is ever made."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import stripe

from src.config.config import Config
from src.services.billing import card_auto_topup as cat
from src.services.billing.payments import StripeService

USER_ID = 41
PM = "pm_test_placeholder"
CUS = "cus_test_placeholder"


@pytest.fixture(autouse=True)
def _feature_on(monkeypatch):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", True)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ALL_ACCOUNTS", False)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_USER_IDS", frozenset({USER_ID}))
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_DEFAULT_AMOUNT_CENTS", 2000)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_MIN_AMOUNT_CENTS", 500)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_MAX_AMOUNT_CENTS", 50000)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_DAILY_LIMIT_CENTS", 10000)
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0")
    cat._quiet_until.clear()
    yield
    cat._quiet_until.clear()


def _user(**fields):
    base = {
        "id": USER_ID,
        "email": "someone@example.com",
        "stripe_customer_id": CUS,
        "stripe_default_payment_method_id": PM,
        "auto_topup_on": True,
        "auto_topup_paused_reason": None,
    }
    base.update(fields)
    return base


def _intent(status="succeeded", amount=2000, pid="pi_test_1"):
    return stripe.PaymentIntent.construct_from(
        {"id": pid, "object": "payment_intent", "status": status, "amount_received": amount},
        None,
    )


class _Harness:
    """Patches every side effect of a charge attempt and records calls."""

    def __init__(self, user=None, begin=None):
        self.user = user if user is not None else _user()
        self.begin = begin or {
            "status": "started",
            "attempt_id": "a",
            "customer_id": CUS,
            "payment_method_id": PM,
        }
        self.finish = MagicMock(return_value={"status": "x", "changed": True})
        self.add_credits = MagicMock()
        self.update_payment = MagicMock()
        self.notify = MagicMock()
        self.cancel = MagicMock()
        self.begin_mock = MagicMock(return_value=self.begin)
        self.patches = [
            patch.object(cat, "_fresh_user", return_value=self.user),
            patch.object(cat, "_begin", self.begin_mock),
            patch.object(cat, "_finish", self.finish),
            patch.object(cat, "_notify_failure", self.notify),
            patch.object(cat, "_pause_card_unusable"),
            patch("src.db.payments.create_payment", return_value={"id": 9, "status": "pending"}),
            patch("src.db.payments.update_payment_status", self.update_payment),
            patch("src.db.users.add_credits_to_user", self.add_credits),
            patch.object(stripe.PaymentIntent, "cancel", self.cancel),
        ]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


# ==================== Switch and amounts ====================


async def test_off_by_default_means_nothing_runs(monkeypatch):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", False)
    with patch.object(cat, "run_auto_topup") as run:
        added = await cat.auto_topup_async(_user())
    assert added == 0
    run.assert_not_called()
    assert not cat.feature_enabled_for(USER_ID)


async def test_account_outside_the_list_is_not_charged():
    with patch.object(cat, "run_auto_topup") as run:
        added = await cat.auto_topup_async(_user(id=USER_ID + 1))
    assert added == 0
    run.assert_not_called()


def test_star_admits_every_account(monkeypatch):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ALL_ACCOUNTS", True)
    assert cat.feature_enabled_for(999)


@pytest.mark.parametrize(
    "fields",
    [
        {"auto_topup_on": False},
        {"auto_topup_on": None},
        {"stripe_default_payment_method_id": None},
        {"stripe_customer_id": None},
        {"auto_topup_paused_reason": "declined"},
    ],
)
def test_can_charge_needs_switch_card_and_no_pause(fields):
    assert cat.can_charge(_user())
    assert not cat.can_charge(_user(**fields))


def test_legacy_settings_json_never_enables_charging():
    user = _user(auto_topup_on=False, settings={"auto_topup_enabled": True})
    assert not cat.can_charge(user)


def test_amount_and_daily_limit_are_clamped():
    assert cat.effective_amount_cents(_user()) == 2000
    assert cat.effective_amount_cents(_user(auto_topup_amount_cents=100)) == 500
    assert cat.effective_amount_cents(_user(auto_topup_amount_cents=10**9)) == 50000
    assert cat.effective_daily_limit_cents(_user()) == 10000
    assert cat.effective_daily_limit_cents(_user(auto_topup_daily_limit_cents=2500)) == 2500
    assert cat.effective_daily_limit_cents(_user(auto_topup_daily_limit_cents=10**8)) == 10000


def test_needs_topup_and_shortfall():
    assert cat.needs_topup({"allowed": False})
    assert cat.needs_topup({"allowed": True, "capped_max_tokens": 10})
    assert not cat.needs_topup({"allowed": True})
    assert cat.shortfall_usd({"allowed": False, "shortfall": 1.5}) == 1.5
    assert cat.shortfall_usd({"allowed": True, "capped_max_tokens": 10}) == 0.0


# ==================== Charging ====================


def test_successful_charge_credits_once_with_the_webhook_key():
    with _Harness() as h, patch.object(
        stripe.PaymentIntent, "create", return_value=_intent()
    ) as create:
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.SUCCEEDED
    assert result.credited_usd == Decimal("20.0")
    kwargs = create.call_args.kwargs
    assert kwargs["off_session"] is True and kwargs["confirm"] is True
    assert kwargs["amount"] == 2000 and kwargs["customer"] == CUS
    assert kwargs["payment_method"] == PM
    assert kwargs["idempotency_key"].startswith("gatewayz-auto-topup-")
    assert kwargs["metadata"]["source"] == "auto_topup"
    credit = h.add_credits.call_args.kwargs
    assert credit["request_id"] == StripeService._grant_idempotency_key("pi:pi_test_1")
    assert credit["transaction_type"] == "purchase"
    assert h.finish.call_args.args[1] == cat.SUCCEEDED
    h.notify.assert_not_called()


def test_top_up_fee_applies_as_for_manual_top_ups(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0.05")
    with _Harness(), patch.object(stripe.PaymentIntent, "create", return_value=_intent()):
        result = cat.run_auto_topup(USER_ID)
    assert result.credited_usd == Decimal("19.0")


def test_declined_card_pauses_cancels_and_notifies_once():
    err = stripe.CardError(
        "declined",
        None,
        "card_declined",
        json_body={"error": {"code": "card_declined", "payment_intent": {"id": "pi_test_2"}}},
    )
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create", side_effect=err):
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.DECLINED
    h.add_credits.assert_not_called()
    h.cancel.assert_called_once()
    args, kwargs = h.finish.call_args
    assert args[1] == cat.DECLINED and kwargs["pause"] is True
    h.notify.assert_called_once()


def test_sca_required_is_requires_action():
    err = stripe.CardError(
        "auth",
        None,
        "authentication_required",
        json_body={"error": {"code": "authentication_required", "payment_intent": {"id": "pi_3"}}},
    )
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create", side_effect=err):
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.REQUIRES_ACTION
    assert h.finish.call_args.kwargs["pause"] is True
    h.notify.assert_called_once()


def test_requires_action_status_without_error_is_handled_too():
    with _Harness() as h, patch.object(
        stripe.PaymentIntent, "create", return_value=_intent(status="requires_action")
    ):
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.REQUIRES_ACTION
    h.add_credits.assert_not_called()


def test_no_second_notification_when_the_outcome_was_already_recorded():
    err = stripe.CardError("declined", None, "card_declined", json_body={"error": {}})
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create", side_effect=err):
        h.finish.return_value = {"status": "declined", "changed": False}
        cat.run_auto_topup(USER_ID)
    h.notify.assert_not_called()


def test_stripe_unreachable_is_unknown_not_paused():
    err = stripe.APIConnectionError("network")
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create", side_effect=err):
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.UNKNOWN
    assert h.finish.call_args.args[1] == cat.UNKNOWN
    assert h.finish.call_args.kwargs.get("pause", False) is False
    h.notify.assert_not_called()
    h.add_credits.assert_not_called()


def test_removed_card_is_unusable_and_paused():
    err = stripe.InvalidRequestError("no such payment method", "payment_method")
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create", side_effect=err):
        result = cat.run_auto_topup(USER_ID)
        cat._pause_card_unusable.assert_called_once_with(USER_ID, PM)
    assert result.reason == cat.CARD_UNUSABLE
    h.notify.assert_called_once()


@pytest.mark.parametrize("reason", ["daily_limit", "in_flight", "cooldown", "paused", "no_card"])
def test_refused_by_the_database_never_calls_stripe(reason):
    with _Harness(begin={"status": "refused", "reason": reason}), patch.object(
        stripe.PaymentIntent, "create"
    ) as create:
        result = cat.run_auto_topup(USER_ID)
    assert result.status == "refused" and result.reason == reason
    create.assert_not_called()


def test_amount_too_small_for_the_request_charges_nothing():
    with _Harness() as h, patch.object(stripe.PaymentIntent, "create") as create:
        result = cat.run_auto_topup(USER_ID, shortfall=25.0)
    assert result.reason == "amount_too_small"
    h.begin_mock.assert_not_called()
    create.assert_not_called()


def test_amount_mismatch_is_not_credited():
    with _Harness() as h, patch.object(
        stripe.PaymentIntent, "create", return_value=_intent(amount=1)
    ):
        result = cat.run_auto_topup(USER_ID)
    assert result.status == cat.UNKNOWN
    h.add_credits.assert_not_called()


async def test_daily_limit_refusal_is_remembered_by_the_worker():
    refused = cat.TopupResult("refused", reason="daily_limit")
    with patch.object(cat, "run_auto_topup", return_value=refused) as run:
        assert await cat.auto_topup_async(_user()) == 0
        assert await cat.auto_topup_async(_user()) == 0
    assert run.call_count == 1


async def test_async_returns_the_credited_amount():
    ok = cat.TopupResult(cat.SUCCEEDED, credited_usd=Decimal("20"))
    with patch.object(cat, "run_auto_topup", return_value=ok):
        assert await cat.auto_topup_async(_user(), 1.0) == Decimal("20")


async def test_async_never_raises():
    with patch.object(cat, "run_auto_topup", side_effect=RuntimeError("boom")):
        assert await cat.auto_topup_async(_user()) == 0


# ==================== Webhooks ====================


def _webhook_intent(amount=2000, user_id=USER_ID, status="succeeded", error=None):
    data = {
        "id": "pi_test_9",
        "object": "payment_intent",
        "status": status,
        "amount_received": amount,
        "metadata": {
            "source": "auto_topup",
            "auto_topup_attempt_id": "att-1",
            "user_id": str(user_id),
            "payment_id": "9",
        },
    }
    if error:
        data["last_payment_error"] = error
    return stripe.PaymentIntent.construct_from(data, None)


def test_webhook_success_credits_with_the_same_key():
    attempt = {"id": "att-1", "user_id": USER_ID, "amount_cents": 2000}
    with patch.object(cat, "_attempt", return_value=attempt), patch(
        "src.db.payments.get_payment", return_value={"id": 9, "user_id": USER_ID}
    ), patch("src.db.users.add_credits_to_user") as add, patch(
        "src.db.payments.update_payment_status"
    ), patch.object(cat, "_finish") as finish:
        cat.handle_intent_succeeded(_webhook_intent())
    assert add.call_args.kwargs["request_id"] == StripeService._grant_idempotency_key(
        "pi:pi_test_9"
    )
    assert finish.call_args.args[1] == cat.SUCCEEDED


@pytest.mark.parametrize("intent", [_webhook_intent(amount=1), _webhook_intent(user_id=999)])
def test_webhook_success_that_does_not_match_its_attempt_raises(intent):
    attempt = {"id": "att-1", "user_id": USER_ID, "amount_cents": 2000}
    with patch.object(cat, "_attempt", return_value=attempt), patch(
        "src.db.users.add_credits_to_user"
    ) as add:
        with pytest.raises(ValueError):
            cat.handle_intent_succeeded(intent)
    add.assert_not_called()


def test_webhook_failure_notifies_only_when_it_changed_the_attempt():
    intent = _webhook_intent(status="requires_payment_method", error={"code": "card_declined"})
    for changed, expected in ((True, 1), (False, 0)):
        with patch.object(
            cat,
            "_finish",
            return_value={"changed": changed, "user_id": USER_ID, "amount_cents": 2000},
        ), patch("src.db.payments.get_payment_by_stripe_intent", return_value=None), patch.object(
            cat, "_notify_failure"
        ) as notify, patch.object(cat, "_cancel_quietly"):
            cat.handle_intent_failed(intent)
        assert notify.call_count == expected


def test_stripe_service_routes_only_its_own_events():
    route = StripeService._handle_card_auto_topup_event
    plain_intent = {"id": "pi_x", "metadata": {"user_id": "1", "payment_id": "2"}}
    assert route("payment_intent.succeeded", plain_intent) is False
    assert route("checkout.session.completed", {"mode": "payment", "metadata": {}}) is False
    assert route("invoice.paid", {}) is False
    with patch.object(cat, "handle_intent_succeeded") as handled:
        assert route("payment_intent.succeeded", _webhook_intent()) is True
    handled.assert_called_once()
    setup = {"mode": "setup", "metadata": {"purpose": cat.SETUP_PURPOSE}}
    with patch.object(cat, "handle_setup_session_completed") as handled:
        assert route("checkout.session.completed", setup) is True
    with patch.object(cat, "handle_payment_method_detached") as handled:
        assert route("payment_method.detached", {"id": PM}) is True


def test_setup_mode_checkout_never_grants_credits():
    service = StripeService()
    session = {"id": "cs_test_1", "mode": "setup", "metadata": {"user_id": str(USER_ID)}}
    hydrate = patch.object(
        service, "_hydrate_checkout_session_metadata", return_value=(session, {})
    )
    with hydrate, patch("src.services.billing.payments.add_credits_to_user") as add:
        service._handle_checkout_completed(session)
    add.assert_not_called()


# ==================== Saving a card ====================


def _session(**fields):
    data = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "mode": "setup",
        "status": "complete",
        "customer": CUS,
        "metadata": {"user_id": str(USER_ID), "purpose": cat.SETUP_PURPOSE},
        "setup_intent": {"id": "seti_1", "status": "succeeded", "payment_method": PM},
    }
    data.update(fields)
    return stripe.checkout.Session.construct_from(data, None)


def _pm(customer=CUS):
    return stripe.PaymentMethod.construct_from(
        {
            "id": PM,
            "object": "payment_method",
            "customer": customer,
            "card": {"brand": "visa", "last4": "4242", "exp_month": 12, "exp_year": 2030},
        },
        None,
    )


def test_completed_setup_session_saves_the_card_and_clears_a_pause():
    user = _user(stripe_default_payment_method_id=None, auto_topup_paused_reason="declined")
    with patch.object(cat, "_fresh_user", return_value=user), patch.object(
        stripe.PaymentMethod, "retrieve", return_value=_pm()
    ), patch.object(cat, "_update_user") as update:
        summary = cat.save_card_from_session(_session(), expected_user_id=USER_ID)
    assert summary == {"brand": "visa", "last4": "4242", "exp_month": 12, "exp_year": 2030}
    fields = update.call_args.args[1]
    assert fields["stripe_default_payment_method_id"] == PM
    assert fields["auto_topup_paused_reason"] is None


@pytest.mark.parametrize(
    "session,expected_user",
    [
        (_session(), USER_ID + 1),  # another account's session
        (_session(customer="cus_other"), USER_ID),  # another Stripe customer
        (_session(mode="payment"), USER_ID),
        (_session(metadata={"user_id": str(USER_ID)}), USER_ID),  # not ours
    ],
)
def test_setup_session_that_is_not_this_accounts_is_refused(session, expected_user):
    with patch.object(cat, "_fresh_user", return_value=_user()), patch.object(
        stripe.PaymentMethod, "retrieve", return_value=_pm()
    ), patch.object(cat, "_update_user") as update:
        with pytest.raises(cat.CardSetupError) as err:
            cat.save_card_from_session(session, expected_user_id=expected_user)
    assert err.value.status_code == 404
    update.assert_not_called()


def test_incomplete_setup_session_is_not_saved():
    with patch.object(cat, "_fresh_user", return_value=_user()), patch.object(
        cat, "_update_user"
    ) as update:
        with pytest.raises(cat.CardSetupError) as err:
            cat.save_card_from_session(_session(status="open"), expected_user_id=USER_ID)
    assert err.value.status_code == 409
    update.assert_not_called()


def test_setup_session_uses_fixed_return_urls_and_the_stored_customer():
    with patch.object(cat, "_fresh_user", return_value=_user()), patch.object(
        stripe.checkout.Session,
        "create",
        return_value={"id": "cs_test_2", "url": "https://checkout.example/cs_test_2"},
    ) as create, patch.object(stripe.Customer, "create") as new_customer:
        out = cat.create_setup_session(USER_ID)
    assert out["session_id"] == "cs_test_2"
    kwargs = create.call_args.kwargs
    assert kwargs["mode"] == "setup" and kwargs["customer"] == CUS
    assert kwargs["success_url"].endswith(
        "/settings/credits?card_setup=success&session_id={CHECKOUT_SESSION_ID}"
    )
    new_customer.assert_not_called()


def test_customer_is_created_once_and_stored_by_id():
    client = MagicMock()
    update = client.table.return_value.update.return_value
    update.eq.return_value.is_.return_value.execute.return_value.data = [{"id": USER_ID}]
    new_customer = patch.object(stripe.Customer, "create", return_value={"id": "cus_new"})
    with new_customer as create, patch.object(cat, "_client", return_value=client):
        assert cat.ensure_customer(_user(stripe_customer_id=None)) == "cus_new"
    assert create.call_args.kwargs["idempotency_key"] == f"gatewayz-customer-user-{USER_ID}"
    assert create.call_args.kwargs["metadata"] == {"user_id": str(USER_ID)}


def test_enabling_needs_a_saved_card():
    no_card = _user(stripe_default_payment_method_id=None)
    with patch.object(cat, "_fresh_user", return_value=no_card):
        with pytest.raises(cat.CardSetupError) as err:
            cat.set_preferences(USER_ID, enabled=True, amount_cents=None, daily_limit_cents=None)
    assert err.value.code == "card_required"


@pytest.mark.parametrize(
    "amount,limit,code",
    [(100, None, "invalid_amount"), (None, 10**6, "invalid_daily_limit")],
)
def test_preferences_outside_the_range_are_refused(amount, limit, code):
    with patch.object(cat, "_fresh_user", return_value=_user()), patch.object(cat, "_update_user"):
        with pytest.raises(cat.CardSetupError) as err:
            cat.set_preferences(USER_ID, enabled=True, amount_cents=amount, daily_limit_cents=limit)
    assert err.value.code == code


def test_remove_card_keeps_a_subscription_card_attached():
    for subscribed, detached in ((True, 0), (False, 1)):
        user = _user(
            stripe_subscription_id="sub_test" if subscribed else None,
            subscription_status="active" if subscribed else None,
        )
        with patch.object(cat, "_fresh_user", return_value=user), patch.object(
            cat, "_update_user"
        ) as update, patch.object(stripe.PaymentMethod, "detach") as detach:
            cat.remove_card(USER_ID)
        assert update.call_args.args[1]["auto_topup_on"] is False
        assert detach.call_count == detached


# ==================== Payment-method check, pre-check, notifications ====================


def test_saved_card_with_auto_top_up_counts_as_a_payment_method(monkeypatch):
    from src.services.discount_credits import payment_method as pm

    user = _user(purchased_credits=0, subscription_allowance=0)
    with patch("src.db.users.get_user_by_id", return_value=user):
        assert pm.payment_method_status(USER_ID)["card"] is True
    with patch("src.db.users.get_user_by_id", return_value={**user, "auto_topup_on": False}):
        assert pm.payment_method_status(USER_ID)["has_payment_method"] is False
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", False)
    with patch("src.db.users.get_user_by_id", return_value=user):
        assert pm.payment_method_status(USER_ID)["has_payment_method"] is False


async def test_simple_precheck_tops_up_before_refusing():
    from src.services.billing import simple_metering

    user = _user(subscription_allowance=0, purchased_credits=0)
    with patch("src.db.users.get_user", return_value=user), patch.object(
        cat, "auto_topup_async", return_value=Decimal("20")
    ) as topup:
        assert await simple_metering.precheck_credits("gw_test", 0.5) is user
    topup.assert_awaited_once()


async def test_simple_precheck_still_refuses_when_no_top_up(monkeypatch):
    from fastapi import HTTPException

    from src.services.billing import simple_metering

    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", False)
    user = _user(subscription_allowance=0, purchased_credits=0)
    with patch("src.db.users.get_user", return_value=user):
        with pytest.raises(HTTPException) as err:
            await simple_metering.precheck_credits("gw_test", 0.5)
    assert err.value.status_code == 402


def test_failed_top_up_email_is_off_with_the_notification_switch(monkeypatch):
    from src.services.discount_credits import notifications

    monkeypatch.setattr(Config, "VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED", False)
    with patch.object(notifications.threading, "Thread") as thread:
        notifications.notify_auto_topup_failed(USER_ID, "att", "declined", 2000)
    thread.assert_not_called()


def test_failed_top_up_email_is_sent_once():
    from src.services.discount_credits import notifications

    redis = MagicMock()
    seen: set[str] = set()

    def _set(key, value, nx, ex):
        if key in seen:
            return None
        seen.add(key)
        return True

    redis.set.side_effect = _set
    sent = MagicMock(return_value=MagicMock(sent=True))
    with patch("src.config.redis_config.get_redis_client", return_value=redis), patch(
        "src.db.users.get_user_by_id", return_value={"email": "someone@example.com"}
    ), patch("src.services.email.send_email", sent):
        assert notifications.process_topup_failed(USER_ID, "att-1", "declined", 2000) is True
        assert notifications.process_topup_failed(USER_ID, "att-1", "declined", 2000) is False
        # A second failure the same UTC day is not emailed either.
        assert notifications.process_topup_failed(USER_ID, "att-2", "declined", 2000) is False
    assert sent.call_count == 1
    subject = sent.call_args.args[1]
    assert subject == "Your automatic top-up did not go through"


def test_failed_top_up_email_wording():
    from src.services.discount_credits import notifications

    subject, html_body, text = notifications.build_topup_failed_email("requires_action", 2000)
    assert "$20.00" in text and "confirm the payment" in text
    assert "paused" in text
