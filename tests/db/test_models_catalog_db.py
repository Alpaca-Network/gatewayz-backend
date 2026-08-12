"""Tests for get_candidate_models (gatewayz-backend#2212).

Pure unit tests against a fully mocked Supabase client — no real DB.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py — this is a pure unit test with everything mocked, so
    we do not need a real Supabase connection."""
    return None


def _chainable_query_mock(execute_return):
    """A MagicMock whose builder methods (eq/is_/contains/gte/limit/...) all
    return itself, so any chain order terminates in `.execute()`."""
    query = MagicMock()
    for method in ("select", "eq", "is_", "contains", "gte", "limit", "in_"):
        getattr(query, method).return_value = query
    query.execute.return_value = execute_return
    return query


def _mock_client_for(models_data, pricing_data=None):
    models_query = _chainable_query_mock(MagicMock(data=models_data))
    pricing_query = _chainable_query_mock(MagicMock(data=pricing_data or []))

    def table_side_effect(name):
        if name == "models":
            return models_query
        if name == "model_pricing":
            return pricing_query
        raise AssertionError(f"unexpected table: {name}")

    client = MagicMock()
    client.table.side_effect = table_side_effect
    return client, models_query, pricing_query


def test_no_filters_returns_active_models(sb):
    fake_rows = [{"id": 1, "model_name": "a"}, {"id": 2, "model_name": "b"}]
    client, models_query, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models()

    assert result == fake_rows
    models_query.contains.assert_not_called()
    models_query.gte.assert_not_called()


def test_quality_tier_filters_via_categories_contains(sb):
    fake_rows = [{"id": 1, "model_name": "flagship-model"}]
    client, models_query, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(quality_tier="flagship")

    assert result == fake_rows
    models_query.contains.assert_called_once_with("categories", ["flagship"])


def test_min_context_length_applies_gte_filter(sb):
    fake_rows = [{"id": 1, "model_name": "long-ctx", "context_length": 200_000}]
    client, models_query, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(min_context_length=128_000)

    assert result == fake_rows
    models_query.gte.assert_called_once_with("context_length", 128_000)


def test_required_capabilities_filters_out_non_matching_models(sb):
    fake_rows = [
        {"id": 1, "model_name": "tool-model", "supports_tools": True},
        {"id": 2, "model_name": "no-tools-model", "supports_tools": False},
    ]
    client, _, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(required_capabilities={"tools"})

    assert [m["id"] for m in result] == [1]


def test_required_capabilities_falls_back_to_family_heuristic(sb):
    """No explicit flag on the row -> model_capability_surface's family match decides."""
    fake_rows = [
        {"id": "anthropic/claude-3-opus", "model_name": "claude-3-opus"},
        {"id": "some-provider/unknown-model", "model_name": "unknown-model"},
    ]
    client, _, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(required_capabilities={"vision"})

    assert [m["id"] for m in result] == ["anthropic/claude-3-opus"]


def test_cost_ceiling_excludes_expensive_and_unpriced_then_sorts_cheapest_first(sb):
    fake_rows = [
        {"id": 1, "model_name": "expensive"},
        {"id": 2, "model_name": "cheap"},
        {"id": 3, "model_name": "unpriced"},
    ]
    pricing_rows = [
        # blended = 10*0.25 + 30*0.75 = 25 (per-token, *1e6 applied in code)
        {"model_id": 1, "price_per_input_token": 0.00001, "price_per_output_token": 0.00003},
        # blended = 1*0.25 + 2*0.75 = 1.75
        {"model_id": 2, "price_per_input_token": 0.000001, "price_per_output_token": 0.000002},
        # id 3 has no pricing row at all
    ]
    client, _, pricing_query = _mock_client_for(fake_rows, pricing_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(cost_ceiling=20.0)

    assert [m["id"] for m in result] == [2]
    pricing_query.in_.assert_called_once_with("model_id", [1, 2, 3])


def test_limit_caps_returned_rows(sb):
    fake_rows = [{"id": i, "model_name": f"m{i}"} for i in range(5)]
    client, _, _ = _mock_client_for(fake_rows)

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models(limit=2)

    assert len(result) == 2


def test_returns_empty_list_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")

    with patch("src.db.models_catalog_db.get_client_for_query", return_value=client):
        from src.db.models_catalog_db import get_candidate_models

        result = get_candidate_models()

    assert result == []
