#!/usr/bin/env python3
"""
Tests for the legacy `users.credits` balance migration.

Older accounts hold their balance in the legacy `credits` column, which the
inference credit gates and atomic deduction RPC cannot see — those users were
blocked with 402 "Insufficient credits" while their dashboard showed a
positive balance. `_migrate_legacy_credit_balance` persists the legacy value
into the tiered fields (subscription_allowance / purchased_credits).
"""

from unittest.mock import Mock

from src.db.users import _migrate_legacy_credit_balance


def _mock_client(update_returns_rows: bool = True):
    client = Mock()
    table_mock = Mock()
    client.table.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.execute.return_value = Mock(data=[{"id": 1}] if update_returns_rows else [])
    return client, table_mock


def test_migrates_legacy_credits_to_purchased_for_basic_user():
    client, table_mock = _mock_client()
    user = {
        "id": 1,
        "tier": "basic",
        "subscription_status": None,
        "credits": 12.5,
        "subscription_allowance": 0,
        "purchased_credits": 0,
    }

    _migrate_legacy_credit_balance(client, user)

    table_mock.update.assert_called_once_with({"purchased_credits": 12.5, "credits": 0})
    # Guarded update: id + credits unchanged since read
    eq_calls = {call.args for call in table_mock.eq.call_args_list}
    assert ("id", 1) in eq_calls
    assert ("credits", 12.5) in eq_calls
    # In-memory user reflects the migration for this request
    assert user["purchased_credits"] == 12.5
    assert user["credits"] == 0


def test_migrates_legacy_credits_to_allowance_for_active_pro_user():
    client, table_mock = _mock_client()
    user = {
        "id": 2,
        "tier": "pro",
        "subscription_status": "active",
        "credits": 30.0,
        "subscription_allowance": 0,
        "purchased_credits": 0,
    }

    _migrate_legacy_credit_balance(client, user)

    table_mock.update.assert_called_once_with({"subscription_allowance": 30.0, "credits": 0})
    assert user["subscription_allowance"] == 30.0
    assert user["credits"] == 0


def test_no_migration_when_tiered_balance_exists():
    client, table_mock = _mock_client()
    user = {
        "id": 3,
        "tier": "basic",
        "credits": 10.0,
        "subscription_allowance": 0,
        "purchased_credits": 5.0,
    }

    _migrate_legacy_credit_balance(client, user)

    table_mock.update.assert_not_called()
    assert user["purchased_credits"] == 5.0
    assert user["credits"] == 10.0


def test_no_migration_when_no_legacy_credits():
    client, table_mock = _mock_client()
    user = {
        "id": 4,
        "tier": "basic",
        "credits": 0,
        "subscription_allowance": 0,
        "purchased_credits": 0,
    }

    _migrate_legacy_credit_balance(client, user)

    table_mock.update.assert_not_called()


def test_lost_migration_race_keeps_unmigrated_view():
    """When the guarded update matches no rows (another request already
    migrated), the in-memory user must NOT be granted the balance again."""
    client, table_mock = _mock_client(update_returns_rows=False)
    user = {
        "id": 5,
        "tier": "basic",
        "subscription_status": None,
        "credits": 12.5,
        "subscription_allowance": 0,
        "purchased_credits": 0,
    }

    _migrate_legacy_credit_balance(client, user)

    assert user["purchased_credits"] == 0
    assert user["credits"] == 12.5


def test_migration_failure_is_non_fatal():
    client = Mock()
    client.table.side_effect = RuntimeError("db down")
    user = {
        "id": 6,
        "tier": "basic",
        "credits": 12.5,
        "subscription_allowance": 0,
        "purchased_credits": 0,
    }

    # Must not raise — auth path continues with the un-migrated view
    _migrate_legacy_credit_balance(client, user)
    assert user["credits"] == 12.5
