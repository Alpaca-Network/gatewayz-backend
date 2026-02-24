"""
Test payment credit value validation logic

Tests for the fix to discount validation logic to ensure credit_value
must be at least 100% of payment amount (not 50%).
"""

import pytest
from pydantic import ValidationError

from src.schemas.payments import CreateCheckoutSessionRequest


def test_credit_value_minimum_100_percent():
    """Test that credit value must be at least 100% of payment amount."""
    # $10 payment, $10 credit (100% - minimum allowed)
    request = CreateCheckoutSessionRequest(
        amount=1000,  # $10 in cents
        credit_value=10.00,  # $10 credit
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert request.credit_value == 10.00


def test_credit_value_less_than_payment_rejected():
    """Test that credit value less than payment amount is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        CreateCheckoutSessionRequest(
            amount=1000,  # $10 in cents
            credit_value=5.00,  # $5 credit (50% - should be rejected)
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    error = exc_info.value
    assert "cannot be less than payment amount" in str(error)


def test_credit_value_bonus_packages_allowed():
    """Test that bonus packages (credit > payment) are allowed."""
    test_cases = [
        (1000, 10.00),  # 100% - exact match
        (1000, 12.50),  # 125% - small bonus
        (2000, 25.00),  # 125% - bonus package
        (5000, 60.00),  # 120% - bonus package
        (10000, 150.00),  # 150% - large bonus package
        (10000, 200.00),  # 200% - double value package
        (10000, 300.00),  # 300% - maximum allowed (3x)
    ]

    for amount_cents, credit_value_dollars in test_cases:
        request = CreateCheckoutSessionRequest(
            amount=amount_cents,
            credit_value=credit_value_dollars,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert request.credit_value == credit_value_dollars


def test_credit_value_exceeds_3x_rejected():
    """Test that credit value exceeding 3x payment is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        CreateCheckoutSessionRequest(
            amount=1000,  # $10 in cents
            credit_value=30.01,  # $30.01 credit (just over 3x)
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    error = exc_info.value
    assert "cannot exceed 3x the payment amount" in str(error)


def test_credit_value_exactly_3x_allowed():
    """Test that credit value exactly 3x payment is allowed."""
    request = CreateCheckoutSessionRequest(
        amount=1000,  # $10 in cents
        credit_value=30.00,  # $30 credit (exactly 3x)
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert request.credit_value == 30.00


def test_credit_value_none_allowed():
    """Test that None credit_value is allowed (uses payment amount as credit)."""
    request = CreateCheckoutSessionRequest(
        amount=1000,  # $10 in cents
        credit_value=None,  # Will use payment amount
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert request.credit_value is None


def test_various_payment_amounts_with_valid_credits():
    """Test various payment amounts with valid credit values."""
    test_cases = [
        # (amount_cents, credit_value_dollars, description)
        (50, 0.50, "minimum payment, exact credit"),
        (50, 0.75, "minimum payment, 150% bonus"),
        (100, 1.00, "1 dollar payment, exact credit"),
        (500, 6.00, "5 dollar payment, 120% bonus"),
        (1000, 15.00, "10 dollar payment, 150% bonus"),
        (2500, 50.00, "25 dollar payment, 200% bonus"),
        (10000, 200.00, "100 dollar payment, 200% bonus"),
        (99999999, 999999.99, "maximum payment, exact credit"),
    ]

    for amount_cents, credit_value_dollars, description in test_cases:
        request = CreateCheckoutSessionRequest(
            amount=amount_cents,
            credit_value=credit_value_dollars,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert request.credit_value == credit_value_dollars, f"Failed: {description}"


def test_edge_case_just_below_minimum():
    """Test edge case just below 100% credit value."""
    with pytest.raises(ValidationError) as exc_info:
        CreateCheckoutSessionRequest(
            amount=1000,  # $10 in cents
            credit_value=9.99,  # $9.99 credit (99.9% - just below minimum)
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    error = exc_info.value
    assert "cannot be less than payment amount" in str(error)


def test_edge_case_just_above_maximum():
    """Test edge case just above 3x credit value."""
    with pytest.raises(ValidationError) as exc_info:
        CreateCheckoutSessionRequest(
            amount=1000,  # $10 in cents
            credit_value=30.01,  # $30.01 credit (just over 3x)
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    error = exc_info.value
    assert "cannot exceed 3x the payment amount" in str(error)


def test_fractional_credit_values():
    """Test that fractional credit values are handled correctly."""
    test_cases = [
        (1000, 10.01),  # Just above minimum
        (1000, 12.99),  # Fractional bonus
        (1000, 15.50),  # Half dollar bonus
        (1000, 29.99),  # Just below maximum
    ]

    for amount_cents, credit_value_dollars in test_cases:
        request = CreateCheckoutSessionRequest(
            amount=amount_cents,
            credit_value=credit_value_dollars,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert request.credit_value == credit_value_dollars


def test_promotional_packages_realistic_scenarios():
    """Test realistic promotional package scenarios."""
    promotional_packages = [
        # Common promotional structures
        (1000, 12.50, "10% bonus - $10 → $12.50"),
        (2000, 25.00, "25% bonus - $20 → $25"),
        (5000, 62.50, "25% bonus - $50 → $62.50"),
        (10000, 150.00, "50% bonus - $100 → $150"),
        (20000, 300.00, "50% bonus - $200 → $300"),
        (50000, 750.00, "50% bonus - $500 → $750"),
        (100000, 2000.00, "100% bonus - $1000 → $2000"),
    ]

    for amount_cents, credit_value_dollars, description in promotional_packages:
        request = CreateCheckoutSessionRequest(
            amount=amount_cents,
            credit_value=credit_value_dollars,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert request.credit_value == credit_value_dollars, f"Failed: {description}"
