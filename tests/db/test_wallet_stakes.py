"""Tests for src.db.wallet_stakes (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.wallet_stakes import (
    get_all_wallet_addresses,
    get_stake_totals,
    get_sync_cursor,
    get_sync_cursor_row,
    get_wallet_stake,
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
            query.limit.return_value = query
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


def test_get_wallet_stake_returns_none_when_no_row(sb):
    client = _mock_table_client({"wallet_stakes": []})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_wallet_stake("0xabc") is None


def test_get_wallet_stake_returns_row_and_lowercases_input(sb):
    row = {
        "wallet_address": "0xabc",
        "staked_amount": "123000000000000000000",
        "daily_allowance": "10",
        "last_synced_block": 999,
        "last_synced_at": "2026-09-01T00:00:00+00:00",
    }
    client = _mock_table_client({"wallet_stakes": [row]})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        result = get_wallet_stake("0xABC")

    assert result == row
    table_query = client.table("wallet_stakes")
    args, _ = table_query.eq.call_args
    assert args == ("wallet_address", "0xabc")


def test_get_wallet_stake_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_wallet_stake("0xabc") is None


def test_get_stake_totals_sums_as_int_not_float(sb):
    rows = [
        {"staked_amount": "123000000000000000000"},
        {"staked_amount": "877000000000000000000"},
    ]
    client = _mock_table_client({"wallet_stakes": rows})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        total, count = get_stake_totals()

    assert total == "1000000000000000000000"
    assert count == 2


def test_get_stake_totals_returns_zero_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_stake_totals() == ("0", 0)


def test_get_sync_cursor_row_returns_none_when_no_row(sb):
    client = _mock_table_client({"chain_sync_cursors": []})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor_row("0xcontract") is None


def test_get_sync_cursor_row_returns_row(sb):
    row = {
        "contract_address": "0xcontract",
        "last_synced_block": 999,
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    client = _mock_table_client({"chain_sync_cursors": [row]})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor_row("0xcontract") == row


def test_get_sync_cursor_row_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor_row("0xcontract") is None
