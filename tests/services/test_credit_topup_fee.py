"""Regression tests for the credit top-up fee (OpenRouter-style monetization).

The fee is config-gated via CREDIT_TOPUP_FEE_RATE and defaults to 0.0, so
existing behaviour (1:1 credit grant) must be preserved unless explicitly
enabled. See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md §8.

These tests exercise the REAL production helper (StripeService._apply_topup_fee)
so a change to the fee formula is actually caught here. _apply_topup_fee is a
staticmethod, so no Stripe credentials / service instance are required.
"""

from src.services.billing.payments import StripeService


def _grant_math(amount_dollars: float) -> tuple[float, float]:
    """Call the production helper and return (topup_fee, credits_granted)."""
    _fee_rate, topup_fee, credits_granted = StripeService._apply_topup_fee(amount_dollars)
    return topup_fee, credits_granted


def test_default_no_fee_grants_full_amount(monkeypatch):
    monkeypatch.delenv("CREDIT_TOPUP_FEE_RATE", raising=False)
    fee, granted = _grant_math(10.0)
    assert fee == 0.0
    assert granted == 10.0


def test_five_percent_fee(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0.05")
    fee, granted = _grant_math(10.0)
    assert fee == 0.5
    assert granted == 9.5


def test_five_percent_fee_odd_amount(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0.05")
    fee, granted = _grant_math(7.0)
    assert fee == 0.35
    assert granted == 6.65
    # invariant: fee + granted always reconstructs the paid amount
    assert round(fee + granted, 6) == 7.0


def test_fee_rate_clamped_to_50_percent(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0.99")
    fee, granted = _grant_math(10.0)
    assert fee == 5.0  # clamped to 0.5
    assert granted == 5.0


def test_malformed_fee_rate_falls_back_to_zero(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "not-a-number")
    fee, granted = _grant_math(10.0)
    assert fee == 0.0
    assert granted == 10.0


def test_negative_fee_rate_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "-0.10")
    fee, granted = _grant_math(10.0)
    assert fee == 0.0
    assert granted == 10.0


def test_fee_rate_returned_for_audit(monkeypatch):
    monkeypatch.setenv("CREDIT_TOPUP_FEE_RATE", "0.05")
    fee_rate, topup_fee, credits_granted = StripeService._apply_topup_fee(20.0)
    assert fee_rate == 0.05
    assert topup_fee == 1.0
    assert credits_granted == 19.0
