"""Tests for src/services/discount_credits/payment_method.py -- the payment
method an account needs before it links a wallet for discount credits. The
account row is mocked; nothing calls Stripe or the database."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.services.discount_credits import payment_method as pm

USER_ID = 7


def _user(**fields):
    return {"id": USER_ID, **fields}


@pytest.mark.parametrize(
    "fields",
    [
        {"purchased_credits": 0.01},
        {"subscription_allowance": "3.5"},
        {"purchased_credits": 0, "subscription_allowance": 1},
    ],
)
def test_a_paid_balance_is_a_payment_method(fields):
    with patch("src.db.users.get_user_by_id", return_value=_user(**fields)):
        status = pm.payment_method_status(USER_ID)
    assert status == {"has_payment_method": True, "paid_balance": True, "card": False}
    with patch("src.db.users.get_user_by_id", return_value=_user(**fields)):
        pm.require_payment_method(USER_ID)


def test_an_active_subscription_is_a_card_on_file():
    user = _user(
        stripe_customer_id="cus_test_placeholder",
        stripe_subscription_id="sub_test_placeholder",
        subscription_status="active",
        purchased_credits=0,
        subscription_allowance=0,
    )
    with patch("src.db.users.get_user_by_id", return_value=user):
        status = pm.payment_method_status(USER_ID)
    assert status == {"has_payment_method": True, "paid_balance": False, "card": True}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"purchased_credits": 0, "subscription_allowance": 0},
        {"purchased_credits": None, "subscription_allowance": None},
        {"purchased_credits": "not a number"},
        {"purchased_credits": "NaN"},
        {"purchased_credits": -5},
        # A Stripe customer alone (no subscription) is not a card we can rely on.
        {"stripe_customer_id": "cus_test_placeholder"},
        # A subscription that is not active.
        {
            "stripe_customer_id": "cus_test_placeholder",
            "stripe_subscription_id": "sub_test_placeholder",
            "subscription_status": "canceled",
        },
        {
            "stripe_customer_id": "cus_test_placeholder",
            "stripe_subscription_id": "sub_test_placeholder",
            "subscription_status": "past_due",
        },
    ],
)
def test_no_payment_method_is_a_typed_402(fields):
    with patch("src.db.users.get_user_by_id", return_value=_user(**fields)):
        with pytest.raises(HTTPException) as exc:
            pm.require_payment_method(USER_ID)
    assert exc.value.status_code == 402
    error = exc.value.detail["error"]
    assert error["type"] == "payment_method_required"
    assert error["code"] == "PAYMENT_METHOD_REQUIRED"
    assert error["status"] == 402
    assert "discount credits" in error["message"].lower()


@pytest.mark.parametrize("lookup", [None, RuntimeError("db down")])
def test_an_unreadable_account_is_refused_with_503(lookup):
    kwargs = {"side_effect": lookup} if isinstance(lookup, Exception) else {"return_value": lookup}
    with patch("src.db.users.get_user_by_id", **kwargs):
        assert pm.payment_method_status(USER_ID) is None
        with pytest.raises(HTTPException) as exc:
            pm.require_payment_method(USER_ID)
    assert exc.value.status_code == 503
    assert exc.value.detail == "payment_method_check_unavailable"


def test_the_message_never_uses_retired_names():
    lowered = pm._MESSAGE.lower()
    for banned in ("inference credits", "free inference", "reward"):
        assert banned not in lowered
