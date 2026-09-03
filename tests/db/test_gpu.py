"""Tests for src.db.gpu (Milestone 4 W-A1, gatewayz-backend#2262)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.db.gpu import (
    adjust_outstanding,
    create_node,
    create_provider,
    get_node,
    get_node_by_token_hash,
    get_provider,
    get_provider_by_user,
    list_active_nodes,
    list_nodes,
    list_providers,
    record_heartbeat,
    select_nodes_for_model,
    set_node_status,
    set_provider_status,
    sweep_liveness,
    update_node,
)


@pytest.fixture
def sb():
    return None


def _mock_table_client(table_data: dict):
    """Same helper as tests/db/test_wallet_stakes.py -- one query mock per
    table name, cached so later assertions see the mock the function under
    test actually used."""
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.neq.return_value = query
            query.in_.return_value = query
            query.order.return_value = query
            query.insert.return_value = query
            query.update.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


# ---------------------------------------------------------------------------
# gpu_providers
# ---------------------------------------------------------------------------


def test_create_provider_lowercases_wallet_and_defaults_pending(sb):
    client = _mock_table_client({"gpu_providers": [{"id": 1, "status": "pending"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = create_provider(
            user_id=5,
            display_name="Acme GPUs",
            payout_wallet_address="0xABCDEF0000000000000000000000000000000A",
        )

    args, _ = client.table("gpu_providers").insert.call_args
    assert args[0]["payout_wallet_address"] == "0xabcdef0000000000000000000000000000000a"
    assert args[0]["status"] == "pending"
    assert result == {"id": 1, "status": "pending"}


def test_create_provider_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert (
            create_provider(user_id=5, display_name="x", payout_wallet_address="0x" + "a" * 40)
            is None
        )


def test_get_provider_by_user_returns_none_when_missing(sb):
    client = _mock_table_client({"gpu_providers": []})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert get_provider_by_user(5) is None


def test_set_provider_status_approved_sets_approved_at_and_by(sb):
    client = _mock_table_client({"gpu_providers": [{"id": 1, "status": "approved"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = set_provider_status(1, "approved", approved_by=9)

    args, _ = client.table("gpu_providers").update.call_args
    assert args[0]["status"] == "approved"
    assert args[0]["approved_by"] == 9
    assert "approved_at" in args[0]
    assert result == {"id": 1, "status": "approved"}


def test_set_provider_status_suspend_does_not_set_approved_fields(sb):
    client = _mock_table_client({"gpu_providers": [{"id": 1, "status": "suspended"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        set_provider_status(1, "suspended")

    args, _ = client.table("gpu_providers").update.call_args
    assert args[0] == {"status": "suspended"}


def test_list_providers_filters_by_status(sb):
    client = _mock_table_client({"gpu_providers": [{"id": 1, "status": "pending"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = list_providers(status="pending")

    client.table("gpu_providers").eq.assert_called_with("status", "pending")
    assert result == [{"id": 1, "status": "pending"}]


def test_get_provider_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert get_provider(1) is None


# ---------------------------------------------------------------------------
# gpu_nodes
# ---------------------------------------------------------------------------


def test_create_node_defaults_status_registered(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "status": "registered"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = create_node(
            provider_id=1,
            name="node-a",
            region="us-east",
            gpu_model="H100",
            vram_gb=80,
            endpoint_url="https://node.example.com",
            node_token_hash="hash123",
            models=[{"id": "llama-3.1-8b-instruct"}],
        )

    args, _ = client.table("gpu_nodes").insert.call_args
    assert args[0]["status"] == "registered"
    assert args[0]["node_token_hash"] == "hash123"
    assert result == {"id": 10, "status": "registered"}


def test_get_node_by_token_hash_returns_none_when_no_match(sb):
    client = _mock_table_client({"gpu_nodes": []})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert get_node_by_token_hash("nomatch") is None


def test_get_node_by_token_hash_returns_matching_row(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "node_token_hash": "hash123"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = get_node_by_token_hash("hash123")

    client.table("gpu_nodes").eq.assert_called_with("node_token_hash", "hash123")
    assert result == {"id": 10, "node_token_hash": "hash123"}


def test_list_nodes_scopes_to_provider(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "provider_id": 1}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = list_nodes(1)

    client.table("gpu_nodes").eq.assert_called_with("provider_id", 1)
    assert result == [{"id": 10, "provider_id": 1}]


def test_set_node_status_disabled(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "status": "disabled"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = set_node_status(10, "disabled")

    args, _ = client.table("gpu_nodes").update.call_args
    assert args[0] == {"status": "disabled"}
    assert result == {"id": 10, "status": "disabled"}


def test_record_heartbeat_sets_active_and_outstanding_but_not_attested_column(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "status": "active"}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        record_heartbeat(10, outstanding=3, models=[{"id": "m"}], attested=True)

    args, _ = client.table("gpu_nodes").update.call_args
    payload = args[0]
    assert payload["status"] == "active"
    assert payload["outstanding_requests"] == 3
    assert payload["models"] == [{"id": "m"}]
    assert "attested" not in payload
    assert "attested_heartbeat" not in payload


def test_record_heartbeat_clamps_negative_outstanding_to_zero(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        record_heartbeat(10, outstanding=-5)

    args, _ = client.table("gpu_nodes").update.call_args
    assert args[0]["outstanding_requests"] == 0


def test_adjust_outstanding_increments_and_clamps_at_zero(sb):
    client = _mock_table_client({"gpu_nodes": [{"id": 10, "outstanding_requests": 2}]})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        adjust_outstanding(10, -10)

    args, _ = client.table("gpu_nodes").update.call_args
    assert args[0]["outstanding_requests"] == 0


def test_adjust_outstanding_returns_none_when_node_missing(sb):
    client = _mock_table_client({"gpu_nodes": []})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert adjust_outstanding(999, 1) is None


def test_update_node_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert update_node(10, {"name": "x"}) is None


def test_get_node_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert get_node(10) is None


# ---------------------------------------------------------------------------
# select_nodes_for_model
# ---------------------------------------------------------------------------


def test_select_nodes_for_model_filters_by_approval_and_model_and_sorts(sb):
    nodes = [
        {
            "id": 1,
            "provider_id": 100,
            "status": "active",
            "outstanding_requests": 5,
            "health_score": 90,
            "models": [{"id": "llama-3.1-8b-instruct"}],
        },
        {
            "id": 2,
            "provider_id": 100,
            "status": "active",
            "outstanding_requests": 1,
            "health_score": 50,
            "models": [{"id": "llama-3.1-8b-instruct"}],
        },
        # Different model -- excluded.
        {
            "id": 3,
            "provider_id": 100,
            "status": "active",
            "outstanding_requests": 0,
            "health_score": 100,
            "models": [{"id": "other-model"}],
        },
        # Provider not approved (id 200 excluded from providers table below).
        {
            "id": 4,
            "provider_id": 200,
            "status": "active",
            "outstanding_requests": 0,
            "health_score": 100,
            "models": [{"id": "llama-3.1-8b-instruct"}],
        },
    ]
    providers = [{"id": 100}]  # only provider 100 is 'approved'

    client = _mock_table_client({"gpu_nodes": nodes, "gpu_providers": providers})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = select_nodes_for_model("llama-3.1-8b-instruct")

    assert [n["id"] for n in result] == [2, 1]  # node 2: lower outstanding wins


def test_select_nodes_for_model_returns_empty_when_no_active_nodes(sb):
    client = _mock_table_client({"gpu_nodes": []})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert select_nodes_for_model("anything") == []


def test_select_nodes_for_model_returns_empty_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert select_nodes_for_model("anything") == []


# ---------------------------------------------------------------------------
# list_active_nodes
# ---------------------------------------------------------------------------


def test_list_active_nodes_filters_by_approval_only(sb):
    nodes = [
        {"id": 1, "provider_id": 100, "status": "active", "name": "a"},
        {"id": 2, "provider_id": 200, "status": "active", "name": "b"},  # provider not approved
    ]
    providers = [{"id": 100}]  # only provider 100 is 'approved'

    client = _mock_table_client({"gpu_nodes": nodes, "gpu_providers": providers})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        result = list_active_nodes()

    assert [n["id"] for n in result] == [1]
    client.table("gpu_nodes").eq.assert_called_with("status", "active")


def test_list_active_nodes_returns_empty_when_no_active_nodes(sb):
    client = _mock_table_client({"gpu_nodes": []})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert list_active_nodes() == []


def test_list_active_nodes_returns_empty_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert list_active_nodes() == []


# ---------------------------------------------------------------------------
# sweep_liveness
# ---------------------------------------------------------------------------


def test_sweep_liveness_degrades_and_offlines_by_age(sb):
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
    fresh = (now - timedelta(seconds=30)).isoformat()
    stale_degraded = (now - timedelta(seconds=200)).isoformat()  # >= 180s
    stale_offline = (now - timedelta(seconds=700)).isoformat()  # >= 600s

    candidates = [
        {"id": 1, "status": "active", "last_heartbeat_at": fresh},
        {"id": 2, "status": "active", "last_heartbeat_at": stale_degraded},
        {"id": 3, "status": "active", "last_heartbeat_at": stale_offline},
        {"id": 4, "status": "degraded", "last_heartbeat_at": stale_offline},
        {"id": 5, "status": "active", "last_heartbeat_at": None},  # never really heartbeat-aged
    ]
    client = _mock_table_client({"gpu_nodes": candidates})
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        n_degraded, n_offline = sweep_liveness(now, degraded_after_s=180, offline_after_s=600)

    assert n_degraded == 1  # node 2
    assert n_offline == 2  # nodes 3 and 4

    # Only 'active'/'degraded' nodes were ever queried.
    client.table("gpu_nodes").in_.assert_any_call("status", ["active", "degraded"])


def test_sweep_liveness_returns_zero_zero_on_lookup_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert sweep_liveness(datetime.now(UTC), 180, 600) == (0, 0)


def test_sweep_liveness_no_transitions_when_all_fresh(sb):
    now = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
    fresh = (now - timedelta(seconds=5)).isoformat()
    client = _mock_table_client(
        {"gpu_nodes": [{"id": 1, "status": "active", "last_heartbeat_at": fresh}]}
    )
    with patch("src.db.gpu.get_supabase_client", return_value=client):
        assert sweep_liveness(now, 180, 600) == (0, 0)
