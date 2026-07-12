#!/usr/bin/env python3
"""
Tests for enforce_subscription_status_gate.

Payment-lapse statuses (canceled, past_due, ...) must not lock out prepaid
purchased credits — those are retained on cancellation and were already paid
for. Hard-blocked abuse statuses (bot, suspended) block regardless of balance.
"""

import pytest
from fastapi import HTTPException

from src.security.inference_gates import enforce_subscription_status_gate


def test_active_user_passes():
    enforce_subscription_status_gate({"id": 1, "subscription_status": "active"})


def test_none_user_and_missing_status_pass():
    enforce_subscription_status_gate(None)
    enforce_subscription_status_gate({"id": 1})


def test_canceled_user_without_balance_is_blocked():
    user = {
        "id": 2,
        "subscription_status": "canceled",
        "purchased_credits": 0,
        "subscription_allowance": 0,
        "credits": 0,
    }
    with pytest.raises(HTTPException) as exc_info:
        enforce_subscription_status_gate(user)
    assert exc_info.value.status_code == 403


def test_canceled_user_with_purchased_credits_is_allowed():
    user = {
        "id": 3,
        "subscription_status": "canceled",
        "purchased_credits": 4.20,
        "subscription_allowance": 0,
        "credits": 0,
    }
    enforce_subscription_status_gate(user)


def test_past_due_user_with_purchased_credits_is_allowed():
    user = {
        "id": 4,
        "subscription_status": "past_due",
        "purchased_credits": 1.0,
    }
    enforce_subscription_status_gate(user)


def test_canceled_user_with_only_legacy_credits_is_allowed():
    user = {
        "id": 5,
        "subscription_status": "canceled",
        "purchased_credits": 0,
        "subscription_allowance": 0,
        "credits": 9.99,
    }
    enforce_subscription_status_gate(user)


@pytest.mark.parametrize("status", ["bot", "suspended"])
def test_hard_blocked_statuses_block_even_with_credits(status):
    user = {
        "id": 6,
        "subscription_status": status,
        "purchased_credits": 100.0,
        "credits": 100.0,
    }
    with pytest.raises(HTTPException) as exc_info:
        enforce_subscription_status_gate(user)
    assert exc_info.value.status_code == 403
