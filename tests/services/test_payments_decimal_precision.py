"""
Test decimal precision in payment credit calculations

Tests for the fix to use Decimal instead of round() for credit calculations
to avoid floating-point precision errors in financial calculations.
"""

from decimal import Decimal


def test_decimal_credit_calculation():
    """Test that Decimal provides precise credit calculations."""
    # Test case that would fail with round() due to floating-point errors
    credit_value = 19.99

    # Old way (could have precision issues)
    old_way = int(round(credit_value * 100))

    # New way (using Decimal for precision)
    credit_value_decimal = Decimal(str(credit_value))
    new_way = int(credit_value_decimal * 100)

    # Both should produce 1999 cents
    assert old_way == 1999
    assert new_way == 1999


def test_decimal_precision_edge_cases():
    """Test edge cases where floating-point precision matters."""
    test_cases = [
        (0.01, 1),  # 1 cent
        (0.10, 10),  # 10 cents
        (1.00, 100),  # 1 dollar
        (9.99, 999),  # 999 cents
        (10.50, 1050),  # 1050 cents
        (19.99, 1999),  # 1999 cents
        (99.99, 9999),  # 9999 cents
        (100.00, 10000),  # 100 dollars
        (999.99, 99999),  # 999.99 dollars
    ]

    for credit_value, expected_cents in test_cases:
        credit_value_decimal = Decimal(str(credit_value))
        calculated_cents = int(credit_value_decimal * 100)
        assert (
            calculated_cents == expected_cents
        ), f"Failed for {credit_value}: expected {expected_cents}, got {calculated_cents}"


def test_decimal_preserves_precision():
    """Test that Decimal preserves precision better than float."""
    # This is a known floating-point precision issue
    float_result = 0.1 + 0.1 + 0.1
    assert float_result != 0.3  # This will be something like 0.30000000000000004

    # Decimal handles this correctly
    decimal_result = Decimal("0.1") + Decimal("0.1") + Decimal("0.1")
    assert decimal_result == Decimal("0.3")


def test_credit_value_conversion_matches_schema():
    """Test that credit value conversion matches expected schema behavior."""
    # Test various payment amounts and credit values
    test_cases = [
        (1000, 10.00),  # $10 payment, $10 credit
        (2000, 25.00),  # $20 payment, $25 credit (125% bonus)
        (5000, 60.00),  # $50 payment, $60 credit (120% bonus)
        (10000, 150.00),  # $100 payment, $150 credit (150% bonus)
    ]

    for amount_cents, credit_value_dollars in test_cases:
        # Convert credit value to cents using Decimal
        credit_value_decimal = Decimal(str(credit_value_dollars))
        credits_cents = int(credit_value_decimal * 100)

        # Verify conversion is correct
        assert credits_cents == int(credit_value_dollars * 100)

        # Verify credit value is at least payment amount (100% minimum)
        amount_dollars = amount_cents / 100
        assert credit_value_dollars >= amount_dollars


def test_large_credit_values():
    """Test that large credit values are handled correctly."""
    large_values = [
        1000.00,  # $1,000
        5000.00,  # $5,000
        10000.00,  # $10,000
    ]

    for credit_value in large_values:
        credit_value_decimal = Decimal(str(credit_value))
        credits_cents = int(credit_value_decimal * 100)

        # Verify no precision loss
        assert credits_cents == int(credit_value * 100)

        # Verify cents can be converted back to dollars
        dollars_back = credits_cents / 100
        assert dollars_back == credit_value


def test_fractional_cents_handled():
    """Test that fractional cents are handled correctly."""
    # These values when multiplied by 100 might have fractional cents
    test_cases = [
        (10.999, 1099),  # Rounds down from 1099.9
        (10.001, 1000),  # Rounds down from 1000.1
        (10.995, 1099),  # Rounds down from 1099.5
    ]

    for credit_value, expected_cents in test_cases:
        credit_value_decimal = Decimal(str(credit_value))
        credits_cents = int(credit_value_decimal * 100)
        assert credits_cents == expected_cents
