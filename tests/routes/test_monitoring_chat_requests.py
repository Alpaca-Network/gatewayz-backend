"""Regression tests for /api/monitoring/chat-requests*.

Every handler in this group used to hand PostgREST an unbounded read of
chat_completion_requests (80k rows, joined two levels deep) and aggregate the
result in Python. PostgREST silently truncates such a response at db-max-rows,
so the aggregates were wrong as well as slow, and /chat-requests/models fanned
one request out into a query per row of the 13k-row models table.

These tests pin the two properties that fix depends on:
  * the query builder always receives a bound (a limit/range and a created_at
    floor) before .execute(), and
  * the response shape the admin panel reads is unchanged.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.routes.monitoring import (
    CHAT_REQUESTS_FALLBACK_MAX_MODELS,
    CHAT_REQUESTS_FALLBACK_SCAN_LIMIT,
    CHAT_REQUESTS_MAX_LIMIT,
    CHAT_REQUESTS_PLOT_MAX_POINTS,
)

client = TestClient(app)


def _chainable(data=None, count=None):
    """A query-builder double that records calls and returns itself for chaining."""
    query = MagicMock()
    for method in ("select", "eq", "in_", "ilike", "gte", "lte", "order", "limit", "range"):
        getattr(query, method).return_value = query
    query.execute.return_value.data = data if data is not None else []
    query.execute.return_value.count = count
    return query


def _db_with(table_query, rpc_fails=True):
    """A get_db() double whose .table() returns `table_query` and whose RPCs fail."""
    db = MagicMock()
    db.table.return_value = table_query
    if rpc_fails:
        db.rpc.side_effect = Exception("RPC not available")
    return db


def _bound_calls(query):
    """(has_row_cap, has_created_at_floor) across every call on the builder."""
    names = [call[0] for call in query.method_calls]
    has_row_cap = "limit" in names or "range" in names
    has_floor = any(
        call[0] == "gte" and call[1] and call[1][0] == "created_at" for call in query.method_calls
    )
    return has_row_cap, has_floor


# ---------------------------------------------------------------------------
# /chat-requests
# ---------------------------------------------------------------------------


@patch("src.db.client.get_db")
def test_chat_requests_status_filter_applies_eq(mock_get_db):
    query = _chainable(data=[], count=0)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests?status=failed")

    assert response.status_code == 200
    query.eq.assert_any_call("status", "failed")


@patch("src.db.client.get_db")
def test_chat_requests_is_bounded_without_any_filter(mock_get_db):
    """The unfiltered page query must still carry a row cap and a date floor."""
    query = _chainable(data=[], count=0)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests")

    assert response.status_code == 200
    has_row_cap, has_floor = _bound_calls(query)
    assert has_row_cap, "page query must be limited/ranged"
    assert has_floor, "page query must carry a created_at lower bound"
    query.range.assert_any_call(0, 99)


@patch("src.db.client.get_db")
def test_chat_requests_does_not_select_phantom_models_model_id(mock_get_db):
    """public.models has no model_id column; selecting it made PostgREST 42703."""
    query = _chainable(data=[], count=0)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests")

    assert response.status_code == 200
    selects = [call[1][0] for call in query.method_calls if call[0] == "select"]
    assert selects
    for clause in selects:
        # Inside the embedded models(...) block, "model_id" can only ever appear
        # as the tail of "provider_model_id".
        embedded = clause[clause.index("models!inner(") :]
        assert "model_id" not in embedded.replace("provider_model_id", "")


@patch("src.db.client.get_db")
def test_chat_requests_explicit_start_date_is_not_overridden(mock_get_db):
    query = _chainable(data=[], count=0)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests?start_date=2026-01-01")

    assert response.status_code == 200
    query.gte.assert_any_call("created_at", "2026-01-01")
    assert response.json()["metadata"]["window_days"] is None


@patch("src.db.client.get_db")
def test_chat_requests_count_mirrors_join_filters(mock_get_db):
    """total_count drives the dashboard's pager, so it must honour every filter."""
    query = _chainable(data=[], count=7)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests?provider_id=5&model_name=gpt")

    assert response.status_code == 200
    query.eq.assert_any_call("models.provider_id", 5)
    query.ilike.assert_any_call("models.model_name", "%gpt%")
    assert response.json()["metadata"]["total_count"] == 7


def test_chat_requests_limit_ceiling_is_postgrest_row_cap():
    assert CHAT_REQUESTS_MAX_LIMIT == 1000
    response = client.get(f"/api/monitoring/chat-requests?limit={CHAT_REQUESTS_MAX_LIMIT + 1}")
    assert response.status_code == 422


@patch("src.db.client.get_db")
def test_chat_requests_response_shape_unchanged(mock_get_db):
    row = {
        "id": 1,
        "request_id": "req-1",
        "status": "failed",
        "error_message": "boom",
        "model_id": 42,
        "processing_time_ms": 12,
        "created_at": "2026-09-01T00:00:00+00:00",
        "models": {
            "id": 42,
            "model_name": "GPT-4",
            "provider_model_id": "openai/gpt-4",
            "providers": {"id": 5, "name": "OpenAI", "slug": "openai"},
        },
    }
    query = _chainable(data=[row], count=1)
    mock_get_db.return_value = _db_with(query)

    body = client.get("/api/monitoring/chat-requests").json()

    assert body["success"] is True
    assert body["data"] == [row]
    metadata = body["metadata"]
    for key in ("total_count", "limit", "offset", "returned_count", "filters", "timestamp"):
        assert key in metadata


# ---------------------------------------------------------------------------
# /chat-requests/counts
# ---------------------------------------------------------------------------


@patch("src.db.client.get_db")
def test_counts_prefers_database_aggregation(mock_get_db):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        {
            "model_id": 42,
            "model_name": "GPT-4",
            "model_identifier": "openai/gpt-4",
            "provider_name": "OpenAI",
            "provider_slug": "openai",
            "request_count": 9,
        }
    ]
    mock_get_db.return_value = db

    body = client.get("/api/monitoring/chat-requests/counts").json()

    db.rpc.assert_called_once_with("get_model_request_counts")
    db.table.assert_not_called()
    assert body["metadata"]["method"] == "rpc"
    assert body["metadata"]["total_requests"] == 9
    assert body["data"][0]["request_count"] == 9


@patch("src.db.client.get_db")
def test_counts_fallback_is_bounded(mock_get_db):
    query = _chainable(data=[])
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests/counts")

    assert response.status_code == 200
    has_row_cap, has_floor = _bound_calls(query)
    assert has_row_cap and has_floor
    query.limit.assert_any_call(CHAT_REQUESTS_FALLBACK_SCAN_LIMIT)


@patch("src.db.client.get_db")
def test_counts_fallback_reads_provider_model_id_not_phantom_column(mock_get_db):
    query = _chainable(
        data=[
            {
                "model_id": 42,
                "models": {
                    "id": 42,
                    "model_name": "GPT-4",
                    "provider_model_id": "openai/gpt-4",
                    "providers": {"name": "OpenAI", "slug": "openai"},
                },
            }
        ]
    )
    mock_get_db.return_value = _db_with(query)

    body = client.get("/api/monitoring/chat-requests/counts").json()

    clause = query.select.call_args[0][0]
    assert "provider_model_id" in clause
    assert "model_name, model_id" not in clause
    assert body["data"][0]["model_identifier"] == "openai/gpt-4"
    assert body["data"][0]["request_count"] == 1


# ---------------------------------------------------------------------------
# /chat-requests/providers
# ---------------------------------------------------------------------------


@patch("src.db.client.get_db")
def test_providers_fallback_is_bounded(mock_get_db):
    query = _chainable(data=[], count=0)
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests/providers")

    assert response.status_code == 200
    has_row_cap, has_floor = _bound_calls(query)
    assert has_row_cap and has_floor
    query.limit.assert_any_call(CHAT_REQUESTS_FALLBACK_SCAN_LIMIT)


@patch("src.db.client.get_db")
def test_providers_rpc_shape_unchanged(mock_get_db):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        {
            "provider_id": 5,
            "name": "OpenAI",
            "slug": "openai",
            "models_with_requests": 3,
            "total_requests": 100,
        }
    ]
    mock_get_db.return_value = db

    body = client.get("/api/monitoring/chat-requests/providers").json()

    assert body["success"] is True
    assert body["data"][0]["provider_id"] == 5
    assert body["metadata"]["total_providers"] == 1


# ---------------------------------------------------------------------------
# /chat-requests/models
# ---------------------------------------------------------------------------


@patch("src.db.client.get_db")
def test_models_fallback_does_not_walk_the_models_table(mock_get_db):
    """The candidate set comes from recent requests, never from `SELECT * FROM models`."""
    requests_query = _chainable(data=[{"model_id": 42}] * 5)
    models_query = _chainable(data=[])

    db = MagicMock()
    db.rpc.side_effect = Exception("RPC not available")
    db.table.side_effect = lambda name: (
        requests_query if name == "chat_completion_requests" else models_query
    )
    mock_get_db.return_value = db

    response = client.get("/api/monitoring/chat-requests/models")

    assert response.status_code == 200
    has_row_cap, has_floor = _bound_calls(requests_query)
    assert has_row_cap and has_floor
    # models is only ever queried for an explicit, capped id list
    in_calls = [call for call in models_query.method_calls if call[0] == "in_"]
    assert in_calls, "models must be restricted to the sampled ids"
    column, ids = in_calls[0][1]
    assert column == "id"
    assert 0 < len(ids) <= CHAT_REQUESTS_FALLBACK_MAX_MODELS


@patch("src.db.client.get_db")
def test_models_fallback_caps_candidate_models(mock_get_db):
    """A sample touching thousands of models must not produce thousands of queries."""
    requests_query = _chainable(data=[{"model_id": i} for i in range(5000)])
    models_query = _chainable(data=[])

    db = MagicMock()
    db.rpc.side_effect = Exception("RPC not available")
    db.table.side_effect = lambda name: (
        requests_query if name == "chat_completion_requests" else models_query
    )
    mock_get_db.return_value = db

    assert client.get("/api/monitoring/chat-requests/models").status_code == 200

    _, ids = [c for c in models_query.method_calls if c[0] == "in_"][0][1]
    assert len(ids) == CHAT_REQUESTS_FALLBACK_MAX_MODELS


@patch("src.db.client.get_db")
def test_models_rpc_shape_unchanged(mock_get_db):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = [
        {
            "model_id": 42,
            "model_identifier": "openai/gpt-4",
            "model_name": "GPT-4",
            "provider_model_id": "openai/gpt-4",
            "provider": {"id": 5, "name": "OpenAI", "slug": "openai"},
            "stats": {
                "total_requests": 10,
                "total_input_tokens": 1,
                "total_output_tokens": 2,
                "total_tokens": 3,
                "avg_processing_time_ms": 4.0,
            },
        }
    ]
    mock_get_db.return_value = db

    body = client.get("/api/monitoring/chat-requests/models?provider_id=5").json()

    db.rpc.assert_called_once_with("get_models_with_requests_by_provider", {"p_provider_id": 5})
    entry = body["data"][0]
    assert entry["model_identifier"] == "openai/gpt-4"
    assert entry["stats"]["total_requests"] == 10
    assert entry["provider"]["slug"] == "openai"


@patch("src.db.client.get_db")
def test_models_fallback_reports_provider_model_id_as_identifier(mock_get_db):
    requests_query = _chainable(data=[{"model_id": 42}])
    models_query = _chainable(
        data=[
            {
                "id": 42,
                "model_name": "GPT-4",
                "provider_model_id": "openai/gpt-4",
                "provider_id": 5,
                "providers": {"id": 5, "name": "OpenAI", "slug": "openai"},
            }
        ]
    )

    db = MagicMock()
    db.rpc.side_effect = Exception("RPC not available")
    counts_query = _chainable(data=[], count=3)
    call_log = {"n": 0}

    def table(name):
        if name != "chat_completion_requests":
            return models_query
        call_log["n"] += 1
        return requests_query if call_log["n"] == 1 else counts_query

    db.table.side_effect = table
    mock_get_db.return_value = db

    body = client.get("/api/monitoring/chat-requests/models").json()

    entry = body["data"][0]
    assert entry["model_identifier"] == "openai/gpt-4"
    assert entry["stats"]["total_requests"] == 3
    # the per-model count is windowed too
    _, floor = _bound_calls(counts_query)
    assert floor


# ---------------------------------------------------------------------------
# /chat-requests/plot-data
# ---------------------------------------------------------------------------


@patch("src.db.client.get_db")
def test_plot_data_series_is_capped_and_chronological(mock_get_db):
    rows = [
        {
            "input_tokens": 2,
            "output_tokens": 3,
            "processing_time_ms": 20,
            "created_at": "2026-09-02T00:00:00+00:00",
        },
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "processing_time_ms": 10,
            "created_at": "2026-09-01T00:00:00+00:00",
        },
    ]
    query = _chainable(data=rows)
    mock_get_db.return_value = _db_with(query)

    body = client.get("/api/monitoring/chat-requests/plot-data").json()

    query.limit.assert_any_call(CHAT_REQUESTS_PLOT_MAX_POINTS)
    has_row_cap, has_floor = _bound_calls(query)
    assert has_row_cap and has_floor
    # newest-first from the database, re-ordered oldest-first for the x-axis
    assert body["plot_data"]["timestamps"] == [
        "2026-09-01T00:00:00+00:00",
        "2026-09-02T00:00:00+00:00",
    ]
    assert body["plot_data"]["tokens"] == [2, 5]
    assert body["plot_data"]["latency"] == [10, 20]
    assert body["metadata"]["total_count"] == 2
    assert body["metadata"]["compression"] == "arrays"
    assert "recent_requests" in body


@patch("src.db.client.get_db")
def test_plot_data_pushes_provider_filter_into_the_join(mock_get_db):
    query = _chainable(data=[])
    mock_get_db.return_value = _db_with(query)

    response = client.get("/api/monitoring/chat-requests/plot-data?provider_id=5")

    assert response.status_code == 200
    query.eq.assert_any_call("models.provider_id", 5)
