"""Unit tests for apply_referral_bonus after the no-free-credits policy change.

Referral bonuses no longer grant credits to either party; the function only
records the referral relationship (marks it completed) for attribution.
"""

from unittest.mock import MagicMock, Mock, patch


@patch("src.services.referral.validate_referral_code")
@patch("src.services.referral.get_supabase_client")
def test_apply_referral_bonus_grants_no_credits_but_completes_record(
    mock_get_client, mock_validate
):
    from src.services.referral import apply_referral_bonus

    referrer = {"id": 2, "username": "referrer", "email": "ref@example.com"}
    mock_validate.return_value = (True, None, referrer)

    client = MagicMock()
    mock_get_client.return_value = client

    # User lookup (single .eq chain) returns an existing user.
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(
        data=[{"id": 1}]
    )
    # Pending referral lookup (three .eq chain) returns a pending record.
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = Mock(
        data=[{"id": 55, "status": "pending"}]
    )
    # Completing the referral record must return truthy data.
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = Mock(
        data=[{"id": 55}]
    )

    with patch("src.db.credit_transactions.add_credits") as mock_add_credits:
        success, error, bonus_data = apply_referral_bonus(
            user_id=1, referral_code="ABC123", purchase_amount=10.0
        )

    assert success is True
    assert error is None
    # No credits granted to either party.
    assert bonus_data["user_bonus"] == 0
    assert bonus_data["referrer_bonus"] == 0
    mock_add_credits.assert_not_called()

    # Referral record is still marked completed (attribution preserved).
    update_payloads = [
        c.args[0]
        for c in client.table.return_value.update.call_args_list
        if c.args and isinstance(c.args[0], dict)
    ]
    assert any(p.get("status") == "completed" for p in update_payloads)


@patch("src.services.referral.validate_referral_code")
@patch("src.services.referral.get_supabase_client")
def test_apply_referral_bonus_below_minimum_returns_error(mock_get_client, mock_validate):
    from src.services.referral import MIN_PURCHASE_AMOUNT, apply_referral_bonus

    mock_get_client.return_value = MagicMock()

    success, error, bonus_data = apply_referral_bonus(
        user_id=1, referral_code="ABC123", purchase_amount=MIN_PURCHASE_AMOUNT - 1
    )

    assert success is False
    assert bonus_data is None
    mock_validate.assert_not_called()
