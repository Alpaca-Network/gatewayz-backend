"""
Unit tests for database-based pricing lookup system
Tests issue #895, #896, #897 implementation
"""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services.pricing import (
    _get_pricing_from_cache_fallback,
    _get_pricing_from_database,
    clear_pricing_cache,
    get_model_pricing,
    get_pricing_cache_stats,
)


class TestDatabasePricingLookup:
    """Test database pricing queries"""

    def test_get_pricing_from_database_success(self):
        """Test successful database query returns pricing"""
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            # Mock successful database response
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]

            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            # Test
            result = _get_pricing_from_database("openai/gpt-4", {"openai/gpt-4"})

            # Assert
            assert result is not None
            assert result["prompt"] == 0.00003
            assert result["completion"] == 0.00006
            assert result["found"] is True
            assert result["source"] == "database"

    def test_get_pricing_from_database_not_found(self):
        """Test database query returns None when model not found"""
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            # Mock empty database response
            mock_result = Mock()
            mock_result.data = []

            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            # Test
            result = _get_pricing_from_database("unknown/model", {"unknown/model"})

            # Assert
            assert result is None

    def test_get_pricing_from_database_handles_error(self):
        """Test database query handles exceptions gracefully"""
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            # Mock database error
            mock_client.side_effect = Exception("Database connection failed")

            # Test
            result = _get_pricing_from_database("openai/gpt-4", {"openai/gpt-4"})

            # Assert
            assert result is None


class TestPricingCache:
    """Test pricing cache functionality"""

    def setup_method(self):
        """Clear cache before each test"""
        clear_pricing_cache()

    def test_cache_stores_and_retrieves_pricing(self):
        """Test cache stores and retrieves pricing correctly"""
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_client,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):

            # Mock database response
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]

            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            # First call - should query database
            result1 = get_model_pricing("openai/gpt-4")

            # Second call - should use cache
            result2 = get_model_pricing("openai/gpt-4")

            # Assert both return same pricing
            assert result1["prompt"] == 0.00003
            assert result2["prompt"] == 0.00003

            # Database should only be called once (second call uses cache)
            assert mock_client.call_count == 1

    def test_cache_expiration(self):
        """Test cache expires after TTL"""
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_client,
            patch("src.services.models._is_building_catalog", return_value=False),
            patch("src.services.pricing._pricing_cache_ttl", 1),
        ):  # 1 second TTL

            # Mock database response
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]

            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            # First call
            get_model_pricing("openai/gpt-4")

            # Wait for cache to expire
            time.sleep(1.1)

            # Second call after expiration
            get_model_pricing("openai/gpt-4")

            # Database should be called twice (cache expired)
            assert mock_client.call_count == 2

    def test_clear_pricing_cache(self):
        """Test clearing pricing cache"""
        # Add some cache entries
        from src.services.pricing import _pricing_cache

        _pricing_cache["model1"] = {"data": {"prompt": 0.001}, "timestamp": time.time()}
        _pricing_cache["model2"] = {"data": {"prompt": 0.002}, "timestamp": time.time()}

        # Clear specific entry
        clear_pricing_cache("model1")
        assert "model1" not in _pricing_cache
        assert "model2" in _pricing_cache

        # Clear all
        clear_pricing_cache()
        assert len(_pricing_cache) == 0

    def test_get_pricing_cache_stats(self):
        """Test cache statistics"""
        from src.services.pricing import _pricing_cache

        # Add cache entries
        _pricing_cache["model1"] = {"data": {"prompt": 0.001}, "timestamp": time.time()}
        _pricing_cache["model2"] = {"data": {"prompt": 0.002}, "timestamp": time.time()}

        stats = get_pricing_cache_stats()

        assert stats["cached_models"] == 2
        assert stats["ttl_seconds"] == 300


class TestFallbackMechanism:
    """Test fallback to provider API cache"""

    def setup_method(self):
        """Clear cache before each test"""
        clear_pricing_cache()

    def test_fallback_to_cache_on_database_failure(self):
        """Test fallback to provider API cache when database fails"""
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):

            # Mock database failure
            mock_db.side_effect = Exception("Database connection failed")

            # Mock provider API cache success
            mock_cache.return_value = [
                {"id": "openai/gpt-4", "pricing": {"prompt": 0.00003, "completion": 0.00006}}
            ]

            # Test
            result = get_model_pricing("openai/gpt-4")

            # Assert fallback worked
            assert result["prompt"] == 0.00003
            assert result["completion"] == 0.00006
            assert result["source"] == "cache_fallback"

    def test_fallback_to_default_when_all_fail(self):
        """Test fallback to default pricing when both database and cache fail"""
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):

            # Mock database failure
            mock_db.side_effect = Exception("Database connection failed")

            # Mock cache failure
            mock_cache.return_value = []

            # Test
            result = get_model_pricing("unknown/model")

            # Assert default pricing is used
            assert result["prompt"] == 0.00002
            assert result["completion"] == 0.00002
            assert result["found"] is False
            assert result["source"] == "default"

    def test_database_takes_priority_over_cache(self):
        """Test database is queried before falling back to cache"""
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):

            # Mock database success with different price
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00005,  # Database price
                    "pricing_completion": 0.00010,
                }
            ]

            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_db.return_value.table.return_value = mock_table

            # Mock cache with different price
            mock_cache.return_value = [
                {
                    "id": "openai/gpt-4",
                    "pricing": {
                        "prompt": 0.00003,  # Cache price (different)
                        "completion": 0.00006,
                    },
                }
            ]

            # Test
            result = get_model_pricing("openai/gpt-4")

            # Assert database price is used (not cache)
            assert result["prompt"] == 0.00005
            assert result["completion"] == 0.00010
            assert result["source"] == "database"


class TestModelIDNormalization:
    """Test model ID normalization and alias resolution"""

    def setup_method(self):
        """Clear cache before each test"""
        clear_pricing_cache()

    def test_handles_provider_suffixes(self):
        """Test stripping provider-specific suffixes"""
        with (
            patch("src.services.pricing._get_pricing_from_database") as mock_db,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):

            # Mock database to return None (to see candidate_ids being passed)
            mock_db.return_value = None

            # Mock cache fallback to return None
            with patch("src.services.pricing._get_pricing_from_cache_fallback", return_value=None):
                # Test with suffix
                get_model_pricing("openai/gpt-4:hf-inference")

                # Check that both original and normalized IDs are in candidates
                call_args = mock_db.call_args
                candidate_ids = call_args[0][1]

                assert "openai/gpt-4:hf-inference" in candidate_ids
                assert "openai/gpt-4" in candidate_ids

    def test_free_model_detection(self):
        """Test free models return $0 cost"""
        from src.services.pricing import calculate_cost

        # Test free model (ending in :free)
        cost = calculate_cost("google/gemma-2-9b-it:free", 1000, 500)

        # Assert cost is $0
        assert cost == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
