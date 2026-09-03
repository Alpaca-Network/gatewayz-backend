"""Tests for src.db.user_wallets (gatewayz-backend#2249 #2250 #2251 #2252)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.user_wallets import (
    count_wallets,
    get_wallet,
    get_wallets_for_user,
    link_wallet,
    unlink_wallet,
)


@pytest.fixture
def sb():
    """Opts these tests out of conftest's skip_if_no_database autouse
    fixture (same convention as tests/db/test_wallet_stakes.py) -- they
    fully mock the Supabase client and need no real DB."""
    return None


def _mock_table_client(table_data: dict):
    """table_data maps table name -> the .data a chained query call returns."""
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.order.return_value = query
            query.insert.return_value = query
            query.delete.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_get_wallets_for_user_returns_rows(sb):
    client = _mock_table_client({"user_wallets": [{"wallet_address": "0xabc", "user_id": 1}]})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert get_wallets_for_user(1) == [{"wallet_address": "0xabc", "user_id": 1}]


def test_get_wallets_for_user_returns_empty_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert get_wallets_for_user(1) == []


def test_get_wallet_returns_row_lowercased_lookup(sb):
    client = _mock_table_client({"user_wallets": [{"wallet_address": "0x" + "a" * 40}]})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        row = get_wallet("0x" + "A" * 40)
    assert row == {"wallet_address": "0x" + "a" * 40}
    client.table("user_wallets").eq.assert_called_with("wallet_address", "0x" + "a" * 40)


def test_get_wallet_returns_none_when_unlinked(sb):
    client = _mock_table_client({"user_wallets": []})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert get_wallet("0x" + "a" * 40) is None


def test_get_wallet_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert get_wallet("0x" + "a" * 40) is None


def test_count_wallets_returns_row_count(sb):
    client = _mock_table_client({"user_wallets": [{"id": 1}, {"id": 2}]})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert count_wallets(1) == 2


def test_count_wallets_returns_zero_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert count_wallets(1) == 0


def test_link_wallet_inserts_expected_row_and_returns_it(sb):
    client = _mock_table_client(
        {"user_wallets": [{"user_id": 1, "wallet_address": "0x" + "a" * 40}]}
    )
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        row = link_wallet(1, "0x" + "A" * 40, source="siwe", make_primary=True)

    args, _ = client.table("user_wallets").insert.call_args
    assert args[0] == {
        "user_id": 1,
        "wallet_address": "0x" + "a" * 40,
        "source": "siwe",
        "wallet_client_type": None,
        "is_primary": True,
    }
    assert row == {"user_id": 1, "wallet_address": "0x" + "a" * 40}


def test_link_wallet_returns_none_on_conflict_or_error(sb):
    """A unique-violation (address already linked) and a transient DB
    error both collapse to None -- the caller must check get_wallet()
    first to distinguish them."""
    client = MagicMock()
    client.table.side_effect = RuntimeError("duplicate key value violates unique constraint")
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert link_wallet(1, "0x" + "a" * 40, source="siwe") is None


def test_unlink_wallet_returns_true_when_a_row_was_deleted(sb):
    client = _mock_table_client({"user_wallets": [{"id": 1}]})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert unlink_wallet(1, "0x" + "a" * 40) is True


def test_unlink_wallet_returns_false_when_nothing_matched(sb):
    client = _mock_table_client({"user_wallets": []})
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert unlink_wallet(1, "0x" + "a" * 40) is False


def test_unlink_wallet_returns_false_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.user_wallets.get_supabase_client", return_value=client):
        assert unlink_wallet(1, "0x" + "a" * 40) is False
