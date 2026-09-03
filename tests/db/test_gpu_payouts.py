"""Tests for src.db.gpu_payouts (gatewayz-backend#2265, #2266)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db import gpu_payouts


@pytest.fixture
def sb():
    return None


def _query_mock(data):
    query = MagicMock()
    for method in ("select", "eq", "gte", "lt", "in_", "order", "limit", "insert", "update"):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=data)
    return query


def _client_with(data):
    query = _query_mock(data)
    client = MagicMock()
    client.table.return_value = query
    return client, query


# ---------------------------------------------------------------------------
# payout rates
# ---------------------------------------------------------------------------


def test_get_payout_rate_returns_int(sb):
    client, _ = _client_with([{"wayz_per_1k_tokens": "500000000000000000"}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_payout_rate_wei_per_1k("small") == 500000000000000000


def test_get_payout_rate_returns_none_when_unseeded(sb):
    client, _ = _client_with([])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_payout_rate_wei_per_1k("small") is None


def test_get_payout_rate_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_payout_rate_wei_per_1k("small") is None


# ---------------------------------------------------------------------------
# provider_work
# ---------------------------------------------------------------------------


def test_list_sampled_pending_work_returns_rows(sb):
    rows = [{"id": 1, "verification": "sampled"}]
    client, query = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.list_sampled_pending_work("2026-09-03T00:00:00Z") == rows
    query.eq.assert_any_call("verification", "sampled")


def test_list_agable_pending_work_returns_rows(sb):
    rows = [{"id": 2, "verification": "pending"}]
    client, query = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.list_agable_pending_work("2026-09-02T00:00:00Z") == rows
    query.in_.assert_any_call("verification", ["pending", "sampled"])


def test_set_verification_updates_and_returns_true(sb):
    client, query = _client_with([{"id": 1}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.set_verification(1, "verified") is True
    query.update.assert_called_once_with({"verification": "verified"})


def test_set_verification_returns_false_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.set_verification(1, "verified") is False


def test_node_verification_stats_since_counts_failures(sb):
    rows = [
        {"verification": "failed"},
        {"verification": "verified"},
        {"verification": "failed"},
    ]
    client, _ = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.node_verification_stats_since(5, "2026-09-02T00:00:00Z") == (2, 3)


def test_node_verification_stats_since_returns_zero_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.node_verification_stats_since(5, "x") == (0, 0)


# ---------------------------------------------------------------------------
# gpu_nodes health
# ---------------------------------------------------------------------------


def test_adjust_health_score_applies_delta(sb):
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = MagicMock(data=[{"health_score": 80}])
    query.update.return_value = query
    client = MagicMock()
    client.table.return_value = query
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.adjust_health_score(1, -20) is True
    query.update.assert_called_once_with({"health_score": 60.0})


def test_adjust_health_score_floors_at_zero(sb):
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = MagicMock(data=[{"health_score": 10}])
    query.update.return_value = query
    client = MagicMock()
    client.table.return_value = query
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        gpu_payouts.adjust_health_score(1, -50)
    query.update.assert_called_once_with({"health_score": 0.0})


def test_adjust_health_score_returns_false_when_node_missing(sb):
    client, _ = _client_with([])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.adjust_health_score(999, -20) is False


def test_get_node_falls_back_to_direct_query_when_gpu_module_absent(sb):
    """src.db.gpu doesn't exist yet (W-A1 in progress) -- this is a REAL
    ImportError, not a simulated one, proving the fallback path actually
    engages rather than assuming it does."""
    client, query = _client_with([{"id": 1, "name": "node-a"}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_node(1) == {"id": 1, "name": "node-a"}
    query.eq.assert_called_once_with("id", 1)


def test_disable_node_falls_back_to_direct_update(sb):
    client, query = _client_with([{"id": 1}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.disable_node(1) is True
    query.update.assert_called_once_with({"status": "disabled"})


# ---------------------------------------------------------------------------
# gpu_providers
# ---------------------------------------------------------------------------


def test_get_provider_for_user_falls_back_to_direct_query(sb):
    client, query = _client_with([{"id": 1, "user_id": 42}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_provider_for_user(42) == {"id": 1, "user_id": 42}
    query.eq.assert_called_once_with("user_id", 42)


def test_get_provider_for_user_returns_none_when_absent(sb):
    client, _ = _client_with([])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_provider_for_user(999) is None


def test_list_approved_providers_falls_back_to_direct_query(sb):
    rows = [{"id": 1, "status": "approved"}]
    client, query = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.list_approved_providers() == rows
    query.eq.assert_called_once_with("status", "approved")


# ---------------------------------------------------------------------------
# provider_earnings
# ---------------------------------------------------------------------------


def test_create_earning_inserts_accrued_row(sb):
    client, query = _client_with([{"id": 1, "status": "accrued"}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        result = gpu_payouts.create_earning(1, 10, 5000)
    assert result == {"id": 1, "status": "accrued"}
    query.insert.assert_called_once_with(
        {"provider_id": 1, "work_id": 10, "amount_wei": "5000", "status": "accrued"}
    )


def test_create_earning_returns_none_on_duplicate_work_id(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("duplicate key value violates unique constraint")
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.create_earning(1, 10, 5000) is None


def test_void_earning_for_work_updates_status(sb):
    client, query = _client_with([{"id": 1}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.void_earning_for_work(10) is True
    query.update.assert_called_once_with({"status": "void"})


def test_earnings_totals_sums_by_status(sb):
    rows = [
        {"amount_wei": "1000", "status": "accrued"},
        {"amount_wei": "2000", "status": "accrued"},
        {"amount_wei": "500", "status": "settled"},
        {"amount_wei": "300", "status": "void"},
    ]
    client, _ = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        totals = gpu_payouts.earnings_totals(1)
    assert totals == {"accrued": 3000, "settled": 500, "void": 300}


def test_earnings_totals_zeroed_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.earnings_totals(1) == {"accrued": 0, "settled": 0, "void": 0}


def test_mark_earnings_settled_is_a_noop_for_empty_list(sb):
    assert gpu_payouts.mark_earnings_settled([], 1) is True


def test_mark_earnings_settled_updates_given_ids(sb):
    client, query = _client_with([{"id": 1}, {"id": 2}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.mark_earnings_settled([1, 2], 99) is True
    query.update.assert_called_once_with({"status": "settled", "settlement_id": 99})
    query.in_.assert_called_once_with("id", [1, 2])


# ---------------------------------------------------------------------------
# provider_settlements
# ---------------------------------------------------------------------------


def test_get_pending_settlement_returns_row(sb):
    client, _ = _client_with([{"id": 1, "status": "pending"}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_pending_settlement(1) == {"id": 1, "status": "pending"}


def test_get_pending_settlement_returns_none_when_absent(sb):
    client, _ = _client_with([])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.get_pending_settlement(1) is None


def test_create_settlement_inserts_pending_row(sb):
    client, query = _client_with([{"id": 1, "status": "pending"}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        result = gpu_payouts.create_settlement(
            1, "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", 12000
        )
    assert result == {"id": 1, "status": "pending"}
    query.insert.assert_called_once_with(
        {
            "provider_id": 1,
            "period_start": "2026-09-01T00:00:00Z",
            "period_end": "2026-09-02T00:00:00Z",
            "amount_wei": "12000",
            "status": "pending",
        }
    )


def test_mark_settlement_sent_updates_tx_hash(sb):
    client, query = _client_with([{"id": 1}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.mark_settlement_sent(1, "0xabc") is True
    query.update.assert_called_once_with({"status": "sent", "tx_hash": "0xabc"})


def test_mark_settlement_failed_updates_error(sb):
    client, query = _client_with([{"id": 1}])
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.mark_settlement_failed(1, "insufficient pool balance") is True
    query.update.assert_called_once_with({"status": "failed", "error": "insufficient pool balance"})


def test_list_settlements_for_provider_returns_rows(sb):
    rows = [{"id": 1}, {"id": 2}]
    client, _ = _client_with(rows)
    with patch("src.db.gpu_payouts.get_supabase_client", return_value=client):
        assert gpu_payouts.list_settlements_for_provider(1) == rows
