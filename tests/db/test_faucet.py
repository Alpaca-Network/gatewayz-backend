"""Tests for src.db.faucet (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.faucet import (
    create_pending_claim,
    get_existing_claim,
    has_completed_at_least_one_request,
    mark_claim_failed,
    mark_claim_sent,
)


@pytest.fixture
def sb():
    return None


def _mock_table_client(table_data: dict, raise_on_insert: bool = False):
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.limit.return_value = query
            query.or_.return_value = query
            query.update.return_value = query
            if raise_on_insert and name == "faucet_claims":
                query.insert.side_effect = RuntimeError("duplicate key value")
            else:
                query.insert.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_has_completed_at_least_one_request_true(sb):
    client = _mock_table_client({"usage_records": [{"id": 1}]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is True


def test_has_completed_at_least_one_request_false(sb):
    client = _mock_table_client({"usage_records": []})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is False


def test_has_completed_at_least_one_request_false_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is False


def test_get_existing_claim_returns_none_when_no_row(sb):
    client = _mock_table_client({"faucet_claims": []})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert get_existing_claim(42, "0xabc") is None


def test_get_existing_claim_returns_row_when_present(sb):
    row = {"id": 1, "user_id": 42, "wallet_address": "0xabc", "status": "sent"}
    client = _mock_table_client({"faucet_claims": [row]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert get_existing_claim(42, "0xabc") == row


def test_create_pending_claim_returns_row_on_success(sb):
    inserted = {"id": 7, "user_id": 42, "wallet_address": "0xabc", "status": "pending"}
    client = _mock_table_client({"faucet_claims": [inserted]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        result = create_pending_claim(42, "0xabc", 1000)
    assert result == inserted


def test_create_pending_claim_returns_none_on_unique_violation(sb):
    client = _mock_table_client({}, raise_on_insert=True)
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        result = create_pending_claim(42, "0xabc", 1000)
    assert result is None


def test_mark_claim_sent_updates_status_and_tx_hash(sb):
    client = _mock_table_client({})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        mark_claim_sent(7, "0xtxhash")

    table_query = client.table("faucet_claims")
    args, kwargs = table_query.update.call_args
    assert args[0] == {"status": "sent", "tx_hash": "0xtxhash"}


def test_mark_claim_failed_updates_status_and_error(sb):
    client = _mock_table_client({})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        mark_claim_failed(7, "insufficient funds")

    table_query = client.table("faucet_claims")
    args, kwargs = table_query.update.call_args
    assert args[0] == {"status": "failed", "error": "insufficient funds"}
