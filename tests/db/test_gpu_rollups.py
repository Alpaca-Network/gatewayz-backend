"""Tests for src.db.gpu_rollups (gatewayz-backend#2263 #2264, spec §6).

Mirrors tests/db/test_wallet_stakes.py's MagicMock-chain convention: every
function under test either returns real data or a documented safe default
on a caught DB exception -- never raises.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.db.gpu_rollups import (
    _estimate_uptime_pct,
    _regroup,
    aggregate_hour,
    compute_hourly_aggregates,
    get_public_nodes,
    get_summary,
    get_utilization,
    is_utilization_empty,
    upsert_hourly_rows,
)


@pytest.fixture(autouse=True)
def sb():
    """Marker fixture (see tests/conftest.py's skip_if_no_database, and the
    same convention in tests/db/test_wallet_stakes.py): its mere presence in
    a test's fixtures tells the autouse DB-availability gate that every test
    in this module fully mocks the Supabase client and needs no real
    connection. autouse=True so it applies without changing each test's
    signature.
    """
    return None


def _mock_table_client(table_data: dict, counts: dict | None = None):
    """table_data maps table name -> the .data a chained query returns.
    counts maps table name -> the .count a chained query returns (for
    count="exact" queries). One query mock per table name, so a later
    `client.table(name)` call in a test's assertions returns the SAME mock
    the function under test used.
    """
    counts = counts or {}
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            for method in ("select", "eq", "gte", "lt", "lte", "in_", "order", "limit"):
                getattr(query, method).return_value = query
            query.upsert.return_value = query
            query.execute.return_value = MagicMock(
                data=table_data.get(name, []), count=counts.get(name)
            )
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


# --- compute_hourly_aggregates (pure) ---------------------------------------


def test_compute_hourly_aggregates_groups_by_region_and_model():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    work_rows = [
        {
            "region": "us-east",
            "model": "llama-3.1-8b-instruct",
            "status": "completed",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "latency_ms": 200,
            "node_id": "node-a",
        },
        {
            "region": "us-east",
            "model": "llama-3.1-8b-instruct",
            "status": "failed",
            "prompt_tokens": 80,
            "completion_tokens": 0,
            "latency_ms": 400,
            "node_id": "node-b",
        },
        {
            "region": "eu-west",
            "model": "llama-3.1-8b-instruct",
            "status": "completed",
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "latency_ms": 100,
            "node_id": "node-c",
        },
    ]

    rows = compute_hourly_aggregates(hour, work_rows)
    rows_by_key = {(r["region"], r["model"]): r for r in rows}

    us_east = rows_by_key[("us-east", "llama-3.1-8b-instruct")]
    assert us_east["hour"] == hour.isoformat()
    assert us_east["requests"] == 2
    assert us_east["prompt_tokens"] == 180
    assert us_east["completion_tokens"] == 50
    assert us_east["avg_latency_ms"] == 300  # (200 + 400) / 2
    assert us_east["error_rate"] == 0.5  # 1 of 2 failed
    assert us_east["active_nodes"] == 2  # node-a, node-b

    eu_west = rows_by_key[("eu-west", "llama-3.1-8b-instruct")]
    assert eu_west["requests"] == 1
    assert eu_west["error_rate"] == 0.0
    assert eu_west["active_nodes"] == 1


def test_compute_hourly_aggregates_empty_rows_returns_empty_list():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    assert compute_hourly_aggregates(hour, []) == []


def test_compute_hourly_aggregates_missing_region_buckets_as_unknown():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    rows = compute_hourly_aggregates(
        hour,
        [
            {
                "region": None,
                "model": "m",
                "status": "completed",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "latency_ms": 10,
                "node_id": "n1",
            }
        ],
    )
    assert rows[0]["region"] == "unknown"


def test_compute_hourly_aggregates_no_latency_samples_defaults_to_zero():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    rows = compute_hourly_aggregates(
        hour,
        [
            {
                "region": "us-east",
                "model": "m",
                "status": "completed",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "latency_ms": None,
                "node_id": "n1",
            }
        ],
    )
    assert rows[0]["avg_latency_ms"] == 0


# --- upsert_hourly_rows -------------------------------------------------------


def test_upsert_hourly_rows_no_op_on_empty_list():
    with patch("src.db.gpu_rollups.get_db") as mock_get_db:
        assert upsert_hourly_rows([]) is True
        mock_get_db.assert_not_called()


def test_upsert_hourly_rows_upserts_with_composite_conflict_key():
    client = _mock_table_client({})
    rows = [{"hour": "2026-09-03T17:00:00+00:00", "region": "us-east", "model": "m", "requests": 1}]
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert upsert_hourly_rows(rows) is True

    table_query = client.table("gpu_utilization_hourly")
    args, kwargs = table_query.upsert.call_args
    assert args[0] == rows
    assert kwargs["on_conflict"] == "hour,region,model"


def test_upsert_hourly_rows_returns_false_on_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert upsert_hourly_rows([{"hour": "x", "region": "y", "model": "z"}]) is False


# --- is_utilization_empty -----------------------------------------------------


def test_is_utilization_empty_true_when_zero_rows():
    client = _mock_table_client({}, counts={"gpu_utilization_hourly": 0})
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert is_utilization_empty() is True


def test_is_utilization_empty_false_when_rows_exist():
    client = _mock_table_client({}, counts={"gpu_utilization_hourly": 42})
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert is_utilization_empty() is False


def test_is_utilization_empty_defaults_false_on_error():
    """Safe default is False (not empty) -- an error here must never trigger
    a spurious 7-day backfill."""
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert is_utilization_empty() is False


# --- aggregate_hour ------------------------------------------------------------


def test_aggregate_hour_joins_node_region_and_computes_rows():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    client = _mock_table_client(
        {
            "provider_work": [
                {
                    "model": "m",
                    "status": "completed",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "latency_ms": 100,
                    "node_id": "node-a",
                }
            ],
            "gpu_nodes": [{"id": "node-a", "region": "us-east"}],
        }
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        rows = aggregate_hour(hour)

    assert len(rows) == 1
    assert rows[0]["region"] == "us-east"
    assert rows[0]["model"] == "m"
    assert rows[0]["requests"] == 1

    work_query = client.table("provider_work")
    assert work_query.gte.call_args[0] == ("created_at", hour.isoformat())


def test_aggregate_hour_unknown_node_buckets_as_unknown_region():
    hour = datetime(2026, 9, 3, 17, 0, tzinfo=UTC)
    client = _mock_table_client(
        {
            "provider_work": [
                {
                    "model": "m",
                    "status": "completed",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "latency_ms": 1,
                    "node_id": "missing-node",
                }
            ],
            "gpu_nodes": [],
        }
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        rows = aggregate_hour(hour)
    assert rows[0]["region"] == "unknown"


def test_aggregate_hour_returns_empty_list_on_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert aggregate_hour(datetime(2026, 9, 3, 17, 0, tzinfo=UTC)) == []


# --- get_utilization / _regroup ------------------------------------------------


def test_regroup_by_region_sums_across_models():
    rows = [
        {
            "hour": "2026-09-03T17:00:00+00:00",
            "region": "us-east",
            "model": "a",
            "requests": 10,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "avg_latency_ms": 200,
            "error_rate": 0.1,
            "active_nodes": 2,
        },
        {
            "hour": "2026-09-03T17:00:00+00:00",
            "region": "us-east",
            "model": "b",
            "requests": 10,
            "prompt_tokens": 20,
            "completion_tokens": 20,
            "avg_latency_ms": 400,
            "error_rate": 0.3,
            "active_nodes": 1,
        },
    ]
    out = _regroup(rows, "region")
    assert len(out) == 1
    row = out[0]
    assert row["key"] == "us-east"
    assert row["requests"] == 20
    assert row["prompt_tokens"] == 120
    assert row["avg_latency_ms"] == 300  # weighted: (200*10+400*10)/20
    assert row["error_rate"] == pytest.approx(0.2)  # (0.1*10+0.3*10)/20
    assert row["active_nodes"] == 2  # max, not sum


def test_get_utilization_rejects_invalid_window():
    with pytest.raises(ValueError):
        get_utilization("1h", "region")


def test_get_utilization_rejects_invalid_group():
    with pytest.raises(ValueError):
        get_utilization("24h", "provider")


def test_get_utilization_queries_since_window_and_regroups():
    client = _mock_table_client(
        {
            "gpu_utilization_hourly": [
                {
                    "hour": "2026-09-03T17:00:00+00:00",
                    "region": "us-east",
                    "model": "a",
                    "requests": 5,
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "avg_latency_ms": 100,
                    "error_rate": 0.0,
                    "active_nodes": 1,
                }
            ]
        }
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        rows = get_utilization("24h", "model")

    assert rows == [
        {
            "hour": "2026-09-03T17:00:00+00:00",
            "key": "a",
            "requests": 5,
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "avg_latency_ms": 100,
            "error_rate": 0.0,
            "active_nodes": 1,
        }
    ]


def test_get_utilization_returns_empty_on_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert get_utilization("7d", "region") == []


# --- _estimate_uptime_pct (pure) -----------------------------------------------


def test_estimate_uptime_pct_counts_distinct_active_hours():
    rows = [
        {"hour": "h1", "region": "us-east", "model": "m", "active_nodes": 1},
        {"hour": "h2", "region": "us-east", "model": "m", "active_nodes": 0},
        {"hour": "h3", "region": "us-east", "model": "other-model", "active_nodes": 1},
    ]
    pct = _estimate_uptime_pct("us-east", ["m"], rows)
    assert pct == round(1 / 24 * 100, 2)


def test_estimate_uptime_pct_no_models_returns_zero():
    assert _estimate_uptime_pct("us-east", [], []) == 0.0


def test_estimate_uptime_pct_caps_at_100():
    rows = [
        {"hour": f"h{i}", "region": "us-east", "model": "m", "active_nodes": 1} for i in range(30)
    ]
    assert _estimate_uptime_pct("us-east", ["m"], rows) == 100.0


# --- get_public_nodes / get_summary --------------------------------------------


def test_get_public_nodes_exposes_only_allowlisted_fields():
    client = _mock_table_client(
        {
            "gpu_nodes": [
                {
                    "name": "node-1",
                    "region": "us-east",
                    "gpu_model": "A100",
                    "vram_gb": 80,
                    "status": "active",
                    "models": [{"id": "llama-3.1-8b-instruct", "max_context": 8192}],
                    "gpu_providers": {"status": "approved"},
                }
            ],
            "gpu_utilization_hourly": [],
        }
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        nodes = get_public_nodes()

    assert len(nodes) == 1
    assert set(nodes[0].keys()) == {
        "name",
        "region",
        "gpu_model",
        "vram_gb",
        "status",
        "uptime_24h_pct",
        "models",
    }
    assert nodes[0]["models"] == ["llama-3.1-8b-instruct"]

    nodes_query = client.table("gpu_nodes")
    assert nodes_query.eq.call_args[0] == ("gpu_providers.status", "approved")


def test_get_public_nodes_returns_empty_on_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        assert get_public_nodes() == []


def test_get_summary_counts_active_nodes_and_breaks_down_regions_models():
    client = _mock_table_client(
        {
            "gpu_nodes": [
                {
                    "name": "n1",
                    "region": "us-east",
                    "gpu_model": "A100",
                    "vram_gb": 80,
                    "status": "active",
                    "models": [{"id": "m1"}],
                    "gpu_providers": {"status": "approved"},
                },
                {
                    "name": "n2",
                    "region": "us-east",
                    "gpu_model": "A100",
                    "vram_gb": 80,
                    "status": "degraded",
                    "models": [{"id": "m1"}],
                    "gpu_providers": {"status": "approved"},
                },
            ],
            "gpu_providers": [],
            "gpu_utilization_hourly": [],
        },
        counts={"gpu_providers": 3},
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        summary = get_summary()

    assert summary["active_nodes"] == 1  # only status=='active' counted
    assert summary["approved_providers"] == 3
    assert summary["regions"] == [{"region": "us-east", "nodes": 1}]
    assert summary["models"] == [{"id": "m1", "nodes": 1}]
    assert "updated_at" in summary


def test_get_summary_last_hour_weighted_average():
    client = _mock_table_client(
        {
            "gpu_nodes": [],
            "gpu_providers": [],
            "gpu_utilization_hourly": [
                {
                    "requests": 10,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "avg_latency_ms": 200,
                    "error_rate": 0.1,
                },
                {
                    "requests": 30,
                    "prompt_tokens": 200,
                    "completion_tokens": 100,
                    "avg_latency_ms": 100,
                    "error_rate": 0.0,
                },
            ],
        },
        counts={"gpu_providers": 0},
    )
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        summary = get_summary()

    last_hour = summary["last_hour"]
    assert last_hour["requests"] == 40
    assert last_hour["tokens"] == 450
    assert last_hour["avg_latency_ms"] == 125  # (200*10 + 100*30) / 40
    assert last_hour["error_rate"] == pytest.approx(0.025)  # (0.1*10 + 0*30) / 40


def test_get_summary_no_data_returns_zeros_not_error():
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.gpu_rollups.get_db", return_value=client):
        summary = get_summary()

    assert summary["active_nodes"] == 0
    assert summary["approved_providers"] == 0
    assert summary["regions"] == []
    assert summary["models"] == []
    assert summary["last_hour"] == {
        "requests": 0,
        "tokens": 0,
        "avg_latency_ms": 0,
        "error_rate": 0.0,
    }
