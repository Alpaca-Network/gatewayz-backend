"""Tests for Redis caching layer around get_all_models_for_catalog.

The plan (docs/superpowers/specs/2026-05-25-cost-reduction-design.md) calls for
a 1h Redis cache around the catalog DB read. The plan referenced
`get_catalog`/`set_catalog` in src.services.cache.model_catalog_cache, but those
functions do not exist — the real public helpers for the full active-only
catalog are `get_cached_full_catalog` and `cache_full_catalog`. We adapt to the
real names per the plan's step B1.1 instruction.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py — this is a pure unit test with everything mocked, so
    we do not need a real Supabase connection."""
    return None


def test_get_all_models_for_catalog_uses_cache_on_second_call(sb):
    """Second call within TTL should not hit Supabase."""
    fake_rows = [{"id": 1, "model_name": "test"}]

    with patch("src.db.models_catalog_db.get_client_for_query") as mock_client, patch(
        "src.services.cache.model_catalog_cache.get_cached_full_catalog"
    ) as mock_get, patch(
        "src.services.cache.model_catalog_cache.cache_full_catalog"
    ) as mock_set:

        # First call: cache miss → fetch from Supabase → set cache.
        # Second call: cache hit → return cached data, no Supabase call.
        mock_get.side_effect = [None, fake_rows]

        # Mock the Supabase paginated chain: range().execute() returns one batch
        # then an empty batch to terminate the paginated loop in
        # get_all_models_for_catalog.
        execute_mock = MagicMock()
        execute_mock.side_effect = [
            MagicMock(data=fake_rows),  # first page
            MagicMock(data=[]),  # second page → loop terminates
        ]
        range_mock = MagicMock()
        range_mock.execute = execute_mock
        order_mock = MagicMock()
        order_mock.range.return_value = range_mock
        eq_mock = MagicMock()
        eq_mock.order.return_value = order_mock
        select_mock = MagicMock()
        select_mock.eq.return_value = eq_mock
        select_mock.order.return_value = order_mock
        table_mock = MagicMock()
        table_mock.select.return_value = select_mock
        mock_client.return_value.table.return_value = table_mock

        from src.db.models_catalog_db import get_all_models_for_catalog

        first = get_all_models_for_catalog()
        second = get_all_models_for_catalog()

        assert first == fake_rows
        assert second == fake_rows
        # Supabase touched at most once across both calls
        assert mock_client.call_count <= 1
        mock_set.assert_called_once()
