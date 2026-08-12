"""Tests for src.db.routing_policies (gatewayz-backend#2216)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.routing_policies import get_routing_policy_for_key, is_auto_routing_disabled_for_key


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py — this is a pure unit test with everything mocked, so
    we do not need a real Supabase connection."""
    return None


def _mock_client(key_record_data, policy_data):
    key_query = MagicMock()
    key_query.select.return_value = key_query
    key_query.eq.return_value = key_query
    key_query.execute.return_value = MagicMock(data=key_record_data)

    policy_query = MagicMock()
    policy_query.select.return_value = policy_query
    policy_query.eq.return_value = policy_query
    policy_query.execute.return_value = MagicMock(data=policy_data)

    def table_side_effect(name):
        if name == "api_keys_new":
            return key_query
        if name == "routing_policies":
            return policy_query
        raise AssertionError(f"unexpected table: {name}")

    client = MagicMock()
    client.table.side_effect = table_side_effect
    return client


def test_returns_none_when_key_not_found(sb):
    client = _mock_client(key_record_data=[], policy_data=[])

    with patch("src.db.routing_policies.get_supabase_client", return_value=client):
        result = get_routing_policy_for_key("gw_missing")

    assert result is None


def test_returns_none_when_no_policy_row(sb):
    client = _mock_client(key_record_data=[{"id": 7}], policy_data=[])

    with patch("src.db.routing_policies.get_supabase_client", return_value=client):
        result = get_routing_policy_for_key("gw_live_abc")

    assert result is None


def test_returns_policy_row_when_present(sb):
    row = {"id": 1, "api_key_id": 7, "policy": "balanced", "auto_routing_enabled": False}
    client = _mock_client(key_record_data=[{"id": 7}], policy_data=[row])

    with patch("src.db.routing_policies.get_supabase_client", return_value=client):
        result = get_routing_policy_for_key("gw_live_abc")

    assert result == row


def test_returns_none_on_client_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")

    with patch("src.db.routing_policies.get_supabase_client", return_value=client):
        result = get_routing_policy_for_key("gw_live_abc")

    assert result is None


def test_is_disabled_false_for_no_api_key(sb):
    assert is_auto_routing_disabled_for_key(None) is False


def test_is_disabled_false_when_no_policy_row(sb):
    with patch("src.db.routing_policies.get_routing_policy_for_key", return_value=None):
        assert is_auto_routing_disabled_for_key("gw_live_abc") is False


def test_is_disabled_false_when_flag_explicitly_true(sb):
    with patch(
        "src.db.routing_policies.get_routing_policy_for_key",
        return_value={"auto_routing_enabled": True},
    ):
        assert is_auto_routing_disabled_for_key("gw_live_abc") is False


def test_is_disabled_true_only_when_flag_explicitly_false(sb):
    with patch(
        "src.db.routing_policies.get_routing_policy_for_key",
        return_value={"auto_routing_enabled": False},
    ):
        assert is_auto_routing_disabled_for_key("gw_live_abc") is True


def test_is_disabled_false_when_column_missing_from_row(sb):
    """A routing_policies row that predates the auto_routing_enabled column
    (e.g. one created for the smart_router-only feature) must not accidentally
    disable auto-routing -- missing means "not disabled", not "disabled"."""
    with patch(
        "src.db.routing_policies.get_routing_policy_for_key",
        return_value={"policy": "cost"},
    ):
        assert is_auto_routing_disabled_for_key("gw_live_abc") is False
