#!/usr/bin/env python3
"""Regression tests for Stripe "Basil" API-generation compatibility.

Stripe's Basil API generation (2025-08-27.basil and later, incl.
2025-09-30.clover) made breaking changes that broke the subscription webhook
handlers:

  * ``Subscription.current_period_start`` / ``current_period_end`` were removed
    from the Subscription object and moved onto each subscription *item*.
  * ``Invoice.subscription`` was removed; the subscription now lives under
    ``invoice.parent.subscription_details.subscription`` (and per line under
    ``lines.data[].parent.subscription_item_details.subscription``).

The handlers accessed these fields directly, raising ``AttributeError`` and
aborting before applying the user's tier/allowance. These tests lock in the
Basil-safe accessors and the subscription-mode checkout guard so the regression
cannot return.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.payments import StripeService


@pytest.fixture
def stripe_service():
    with patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake_key"}):
        with patch("stripe.api_key", "sk_test_fake_key"):
            return StripeService()


class _AttrObj:
    """Object whose missing attributes raise AttributeError, like a Stripe object.

    Simulates a Basil-shaped Stripe object where legacy top-level fields are
    absent (attribute access raises), so we exercise the same failure mode the
    production handlers hit.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# --------------------------------------------------------------------------
# _get_subscription_period_end / _get_subscription_period_start
# --------------------------------------------------------------------------

def test_period_end_legacy_top_level(stripe_service):
    sub = {"current_period_end": 1234567890, "items": {"data": []}}
    assert stripe_service._get_subscription_period_end(sub) == 1234567890


def test_period_end_basil_from_items(stripe_service):
    # Basil: no top-level current_period_end; it lives on the item.
    sub = {"items": {"data": [{"current_period_end": 1999999999}]}}
    assert stripe_service._get_subscription_period_end(sub) == 1999999999


def test_period_end_basil_attribute_object(stripe_service):
    # Attribute-style object with the value only on the item (missing top-level
    # attribute would raise AttributeError under direct access).
    item = _AttrObj(current_period_end=1888888888)
    items = _AttrObj(data=[item])
    sub = _AttrObj(items=items)
    assert stripe_service._get_subscription_period_end(sub) == 1888888888


def test_period_end_missing_everywhere(stripe_service):
    assert stripe_service._get_subscription_period_end({"items": {"data": [{}]}}) is None
    assert stripe_service._get_subscription_period_end({}) is None


def test_period_start_basil_from_items(stripe_service):
    sub = {"items": {"data": [{"current_period_start": 1700000000}]}}
    assert stripe_service._get_subscription_period_start(sub) == 1700000000


def test_period_start_legacy_top_level(stripe_service):
    assert stripe_service._get_subscription_period_start({"current_period_start": 42}) == 42


# --------------------------------------------------------------------------
# _get_invoice_subscription_id
# --------------------------------------------------------------------------

def test_invoice_subscription_legacy_string(stripe_service):
    assert stripe_service._get_invoice_subscription_id({"subscription": "sub_legacy"}) == "sub_legacy"


def test_invoice_subscription_legacy_object(stripe_service):
    inv = {"subscription": {"id": "sub_obj"}}
    assert stripe_service._get_invoice_subscription_id(inv) == "sub_obj"


def test_invoice_subscription_basil_parent(stripe_service):
    # Basil: subscription under parent.subscription_details.subscription
    inv = {"parent": {"subscription_details": {"subscription": "sub_basil"}}}
    assert stripe_service._get_invoice_subscription_id(inv) == "sub_basil"


def test_invoice_subscription_basil_lines(stripe_service):
    # Basil fallback: subscription under a line item's parent details.
    inv = {
        "lines": {
            "data": [
                {"parent": {"subscription_item_details": {"subscription": "sub_line"}}}
            ]
        }
    }
    assert stripe_service._get_invoice_subscription_id(inv) == "sub_line"


def test_invoice_subscription_none_for_one_time(stripe_service):
    # A one-time (non-subscription) invoice resolves to None, not a crash.
    assert stripe_service._get_invoice_subscription_id({"lines": {"data": [{}]}}) is None
    assert stripe_service._get_invoice_subscription_id({}) is None


# --------------------------------------------------------------------------
# _handle_checkout_completed: subscription-mode guard (#4)
# --------------------------------------------------------------------------

def test_checkout_completed_skips_subscription_mode(stripe_service):
    """A mode=subscription checkout must NOT be credited as a one-time top-up."""
    session = {"id": "cs_test_sub", "mode": "subscription", "metadata": {"user_id": "3"}}

    with patch.object(
        stripe_service, "_hydrate_checkout_session_metadata", return_value=(session, {"user_id": "3"})
    ), patch("src.db.users.add_credits_to_user") as add_credits, patch(
        "src.services.billing.payments.add_credits_to_user", MagicMock()
    ) as add_credits2:
        result = stripe_service._handle_checkout_completed(session)

    # Handler returns without granting any credits.
    assert result is None
    add_credits.assert_not_called()
    add_credits2.assert_not_called()


def test_checkout_completed_payment_mode_not_skipped(stripe_service):
    """A mode=payment checkout should proceed past the subscription guard."""
    session = {"id": "cs_test_pay", "mode": "payment", "metadata": {}}

    # Force an early, identifiable failure *after* the guard so we can assert the
    # guard did not short-circuit a payment-mode session.
    sentinel = RuntimeError("proceeded past subscription guard")
    with patch.object(
        stripe_service, "_hydrate_checkout_session_metadata", return_value=(session, {})
    ), patch.object(stripe_service, "_coerce_to_int", side_effect=sentinel):
        with pytest.raises(RuntimeError, match="proceeded past subscription guard"):
            stripe_service._handle_checkout_completed(session)
