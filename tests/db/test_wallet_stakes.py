"""Tests for src.db.wallet_stakes (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.wallet_stakes import (
    get_all_wallet_addresses,
    get_sync_cursor,
    set_sync_cursor,
    upsert_wallet_stake,
)


@pytest.fixture
def sb():
    return None


def _mock_table_client(table_data: dict):
    """table_data maps table name -> the .data a chained query call returns.

    Caches one query mock per table name so a later `client.table(name)`
    call in a test's assertions returns the SAME mock the function under
    test used -- not a fresh, uncalled one.
    """
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.upsert.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_get_all_wallet_addresses_returns_list(sb):
    client = _mock_table_client(
        {"wallet_stakes": [{"wallet_address": "0xabc"}, {"wallet_address": "0xdef"}]}
    )
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_all_wallet_addresses() == ["0xabc", "0xdef"]


def test_get_all_wallet_addresses_returns_empty_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_all_wallet_addresses() == []


def test_upsert_wallet_stake_writes_expected_row_and_returns_true(sb):
    client = _mock_table_client({})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        result = upsert_wallet_stake("0xabc", 500, 10, 12345, "2026-09-01T00:00:00+00:00")

    table_query = client.table("wallet_stakes")
    args, kwargs = table_query.upsert.call_args
    assert args[0] == {
        "wallet_address": "0xabc",
        "staked_amount": "500",
        "daily_allowance": "10",
        "last_synced_block": 12345,
        "last_synced_at": "2026-09-01T00:00:00+00:00",
    }
    assert kwargs["on_conflict"] == "wallet_address"
    assert result is True


def test_upsert_wallet_stake_returns_false_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert upsert_wallet_stake("0xabc", 500, 10, 12345, "2026-09-01T00:00:00+00:00") is False


def test_get_sync_cursor_returns_none_when_no_row(sb):
    client = _mock_table_client({"chain_sync_cursors": []})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") is None


def test_get_sync_cursor_returns_block_number(sb):
    client = _mock_table_client({"chain_sync_cursors": [{"last_synced_block": 999}]})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") == 999


def test_get_sync_cursor_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") is None


def test_set_sync_cursor_lowercases_contract_address(sb):
    client = _mock_table_client({})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        set_sync_cursor("0xCONTRACT", 999, "2026-09-01T00:00:00+00:00")

    table_query = client.table("chain_sync_cursors")
    args, kwargs = table_query.upsert.call_args
    assert args[0]["contract_address"] == "0xcontract"
    assert kwargs["on_conflict"] == "contract_address"
