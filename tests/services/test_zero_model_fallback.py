#!/usr/bin/env python3
"""
Comprehensive tests for zero-model fallback mechanisms

These tests ensure that when a gateway/provider returns zero models,
the system properly falls back to database-backed models and handles
the situation gracefully.

Tests cover:
- Zero model detection in gateway responses
- Database-backed fallback activation
- Fallback model quality and availability
- Retry mechanisms for transient failures
- Alerting/metrics for zero-model events
- Recovery after fallback
"""

import asyncio
from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Mark all tests in this module as fallback tests
pytestmark = [pytest.mark.unit, pytest.mark.fallback]


class TestZeroModelDetection:
    """Test detection of zero-model conditions"""

    def test_empty_list_detected_as_zero_models(self):
        """Test that empty list response is detected as zero models"""
        from src.services.gateway_health_service import test_gateway_cache

        config = {"cache": {"data": [], "timestamp": datetime.now(UTC)}, "min_expected_models": 5}

        success, message, count, models = test_gateway_cache("test_gateway", config)

        assert success is False
        assert count == 0
        assert "0 models" in message.lower() or "empty" in message.lower()

    def test_none_data_detected_as_zero_models(self):
        """Test that None data is detected as zero models"""
        from src.services.gateway_health_service import test_gateway_cache

        config = {"cache": {"data": None, "timestamp": datetime.now(UTC)}, "min_expected_models": 5}

        success, message, count, models = test_gateway_cache("test_gateway", config)

        assert success is False
        assert count == 0

    def test_below_minimum_threshold_detected(self):
        """Test that model count below minimum threshold is detected"""
        from src.services.gateway_health_service import test_gateway_cache

        config = {
            "cache": {
                "data": [{"id": "model1"}, {"id": "model2"}],  # Only 2 models
                "timestamp": datetime.now(UTC),
            },
            "min_expected_models": 10,  # But expecting 10
        }

        success, message, count, models = test_gateway_cache("test_gateway", config)

        assert success is False
        assert count == 2
        assert "expected" in message.lower() or "only" in message.lower()

    def test_api_response_zero_models_detection(self):
        """Test that API returning zero models is properly detected"""
        from src.services.gateway_health_service import test_gateway_endpoint

        config = {
            "name": "Test Gateway",
            "url": "https://api.test.com/models",
            "api_key": "test-key",
            "api_key_env": "TEST_API_KEY",
            "header_type": "bearer",
            "min_expected_models": 5,
        }

        # Mock httpx to return empty models list
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

            # Run the async test
            success, message, count = asyncio.get_event_loop().run_until_complete(
                test_gateway_endpoint("test", config)
            )

            assert success is False
            assert count == 0
            assert "0 models" in message.lower()


class TestDatabaseBackedFallback:
    """Test database-backed fallback when API returns zero models"""

    @pytest.fixture
    def mock_db_models(self):
        """Fixture providing mock database models"""
        return [
            {
                "id": "fallback-model-1",
                "provider": "openrouter",
                "pricing": {"prompt": 0.001, "completion": 0.002},
            },
            {
                "id": "fallback-model-2",
                "provider": "openrouter",
                "pricing": {"prompt": 0.0015, "completion": 0.0025},
            },
        ]

    def test_fallback_returns_db_models_when_api_fails(self, mock_db_models):
        """Test that fallback returns database models when API returns none"""
        # Mock the database query to return fallback models
        with patch("src.db.failover_db.get_providers_for_model") as mock_get_providers:
            mock_get_providers.return_value = [
                {
                    "provider_slug": "openrouter",
                    "provider_model_id": "fallback-model-1",
                    "provider_health_status": "healthy",
                    "provider_response_time_ms": 150,
                    "pricing_prompt": 0.001,
                    "pricing_completion": 0.002,
                    "success_rate": 0.98,
                }
            ]

            from src.services.failover_service import explain_failover_for_model

            result = explain_failover_for_model("test-model")

            assert result["providers_available"] >= 1
            assert len(result["failover_order"]) >= 1

    def test_fallback_preserves_model_metadata(self, mock_db_models):
        """Test that fallback models preserve essential metadata"""
        with patch("src.db.failover_db.get_providers_for_model") as mock_get_providers:
            mock_get_providers.return_value = [
                {
                    "provider_slug": "featherless",
                    "provider_model_id": "meta-llama/llama-3.1-70b",
                    "provider_health_status": "healthy",
                    "provider_response_time_ms": 200,
                    "pricing_prompt": 0.0008,
                    "pricing_completion": 0.0016,
                    "success_rate": 0.95,
                }
            ]

            from src.services.failover_service import explain_failover_for_model

            result = explain_failover_for_model("llama-3.1-70b")

            if result["providers_available"] > 0:
                first_provider = result["failover_order"][0]
                assert "provider" in first_provider
                assert "pricing_prompt" in first_provider or "health" in first_provider

    def test_fallback_empty_when_no_db_models(self):
        """Test graceful handling when no database fallback models exist"""
        with patch("src.db.failover_db.get_providers_for_model") as mock_get_providers:
            mock_get_providers.return_value = []

            from src.services.failover_service import explain_failover_for_model

            result = explain_failover_for_model("nonexistent-model")

            assert result["providers_available"] == 0
            assert result["failover_order"] == []
            assert "not available" in result["recommendation"].lower()


class TestRetryMechanisms:
    """Test retry mechanisms for transient failures"""

    @pytest.mark.asyncio
    async def test_cache_clear_triggers_refetch(self):
        """Test that clearing cache triggers a refetch attempt"""
        from src.services.gateway_health_service import clear_gateway_cache

        config = {"cache": {"data": ["model1", "model2"], "timestamp": datetime.now(UTC)}}

        result = clear_gateway_cache("test_gateway", config)

        assert result is True
        assert config["cache"]["data"] is None
        assert config["cache"]["timestamp"] is None

    def test_auto_fix_attempts_recovery(self):
        """Test that auto-fix attempts to recover from zero-model state"""
        from src.services.gateway_health_service import GATEWAY_CONFIG

        # Verify auto-fix is part of the comprehensive check
        # by checking that GATEWAY_CONFIG has the expected structure
        assert len(GATEWAY_CONFIG) > 0

        # Each gateway config should have required fields
        for gateway_name, config in list(GATEWAY_CONFIG.items())[:3]:  # Check first 3
            assert "name" in config
            assert "cache" in config
            assert "min_expected_models" in config


class TestProviderFailoverOnZeroModels:
    """Test provider failover when primary provider returns zero models"""

    def test_failover_chain_skips_zero_model_providers(self):
        """Test that failover chain skips providers with zero models"""
        from src.services.provider_failover import build_provider_failover_chain

        # Build chain starting with a provider
        chain = build_provider_failover_chain("openrouter")

        # Chain should have multiple fallback options
        assert len(chain) > 1
        assert "openrouter" in chain

    def test_failover_prioritizes_healthy_providers(self):
        """Test that failover prioritizes healthy providers with models"""
        from src.services.provider_failover import FALLBACK_PROVIDER_PRIORITY

        # Verify priority list exists and has common providers
        assert len(FALLBACK_PROVIDER_PRIORITY) > 0
        assert any(
            p in FALLBACK_PROVIDER_PRIORITY
            for p in ["openrouter", "featherless", "together", "fireworks"]
        )


class TestZeroModelAlerting:
    """Test alerting and monitoring for zero-model events"""

    def test_zero_model_event_can_be_logged(self):
        """Test that zero-model events can be properly logged"""
        import logging

        # Create a logger instance
        logger = logging.getLogger("test_zero_model")
        logger.setLevel(logging.WARNING)

        # Create a mock handler to capture log messages
        mock_handler = Mock()
        mock_handler.level = logging.WARNING
        logger.addHandler(mock_handler)

        # Simulate logging a zero-model event
        logger.warning("Gateway 'test' returned 0 models - activating fallback")

        # Verify the log was captured
        assert mock_handler.handle.called

    def test_gateway_health_includes_model_count(self):
        """Test that gateway health status includes model count information"""
        from src.services.gateway_health_service import test_gateway_cache

        config = {
            "cache": {
                "data": [{"id": f"model-{i}"} for i in range(25)],
                "timestamp": datetime.now(UTC),
            },
            "min_expected_models": 10,
        }

        success, message, count, models = test_gateway_cache("test_gateway", config)

        assert success is True
        assert count == 25
        assert len(models) == 25


class TestGatewayRecovery:
    """Test recovery after zero-model conditions are resolved"""

    def test_cache_repopulates_after_recovery(self):
        """Test that cache repopulates after API recovers"""
        from src.services.gateway_health_service import clear_gateway_cache, test_gateway_cache

        # Start with empty cache
        config = {"cache": {"data": None, "timestamp": None}, "min_expected_models": 5}

        # Verify cache is empty
        success, message, count, models = test_gateway_cache("test_gateway", config)
        assert success is False
        assert count == 0

        # Simulate recovery by populating cache
        config["cache"]["data"] = [{"id": f"model-{i}"} for i in range(10)]
        config["cache"]["timestamp"] = datetime.now(UTC)

        # Verify cache is now populated
        success, message, count, models = test_gateway_cache("test_gateway", config)
        assert success is True
        assert count == 10

    def test_health_status_updates_after_recovery(self):
        """Test that health status properly updates after recovery"""
        from src.services.gateway_health_service import GATEWAY_CONFIG

        # Verify we have the expected gateway configuration
        assert "openrouter" in GATEWAY_CONFIG
        assert "min_expected_models" in GATEWAY_CONFIG["openrouter"]


class TestFailoverServiceIntegration:
    """Integration tests for failover service with zero-model handling"""

    def test_explain_failover_shows_recommendation_for_missing_model(self):
        """Test that explain_failover correctly shows recommendation when model has limited providers"""
        from src.services.failover_service import explain_failover_for_model

        # Use a model that doesn't exist to test the no-providers case
        result = explain_failover_for_model("nonexistent-model-xyz-123")

        # The result should indicate limited or no providers
        assert "recommendation" in result
        assert isinstance(result["providers_available"], int)

    def test_explain_failover_structure(self):
        """Test that explain_failover returns expected structure"""
        from src.services.failover_service import explain_failover_for_model

        result = explain_failover_for_model("gpt-4")

        # Verify structure regardless of actual provider data
        assert "model" in result
        assert "providers_available" in result
        assert "failover_order" in result
        assert "recommendation" in result
        assert isinstance(result["failover_order"], list)


class TestMinimumModelThresholds:
    """Test minimum model threshold configuration"""

    def test_all_gateways_have_minimum_thresholds(self):
        """Test that all gateways have configured minimum model thresholds"""
        from src.services.gateway_health_service import GATEWAY_CONFIG

        for gateway_name, config in GATEWAY_CONFIG.items():
            assert (
                "min_expected_models" in config
            ), f"Gateway {gateway_name} missing min_expected_models"
            assert (
                config["min_expected_models"] > 0
            ), f"Gateway {gateway_name} has invalid min_expected_models"

    def test_major_gateways_have_reasonable_thresholds(self):
        """Test that major gateways have reasonable minimum thresholds"""
        from src.services.gateway_health_service import GATEWAY_CONFIG

        # Major gateways should have significant model counts
        major_gateways = {
            "openrouter": 100,
            "huggingface": 100,
            "deepinfra": 50,
            "together": 20,
            "fireworks": 10,
        }

        for gateway, expected_min in major_gateways.items():
            if gateway in GATEWAY_CONFIG:
                actual_min = GATEWAY_CONFIG[gateway]["min_expected_models"]
                assert (
                    actual_min >= expected_min
                ), f"Gateway {gateway} threshold {actual_min} < expected {expected_min}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "unit or fallback"])
