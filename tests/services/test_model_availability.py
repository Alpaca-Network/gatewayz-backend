"""
Comprehensive tests for Model Availability service
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestModelAvailability:
    """Test Model Availability service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.model_availability

        assert src.services.model_availability is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import model_availability

        assert hasattr(model_availability, "__name__")


class TestIsModelAvailable:
    """Tests for the is_model_available method"""

    def test_unknown_model_returns_available(self):
        """
        Test that unknown models (not in availability cache) return True.

        This is important to prevent blocking new/unknown models before they're tracked.
        The circuit breaker pattern should only block models that have been observed failing,
        not models we've never seen before.
        """
        from src.services.model_availability import ModelAvailabilityService

        service = ModelAvailabilityService()
        # Model not in cache should return True (optimistic approach)
        result = service.is_model_available("unknown-model-xyz", "openrouter")
        assert result is True, "Unknown models should be assumed available"

    def test_available_model_returns_available(self):
        """Test that models with AVAILABLE status return True"""
        from src.services.model_availability import (
            AvailabilityStatus,
            CircuitBreakerState,
            ModelAvailability,
            ModelAvailabilityService,
        )

        service = ModelAvailabilityService()

        # Add a model to the cache
        availability = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.99,
            response_time_ms=500.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=["gpt-3.5-turbo"],
        )
        service.availability_cache["openrouter:gpt-4"] = availability

        result = service.is_model_available("gpt-4", "openrouter")
        assert result is True

    def test_model_with_open_circuit_returns_unavailable(self):
        """Test that models with OPEN circuit breaker return False"""
        from src.services.model_availability import (
            AvailabilityStatus,
            CircuitBreakerState,
            ModelAvailability,
            ModelAvailabilityService,
        )

        service = ModelAvailabilityService()

        # Add a model with open circuit breaker
        availability = ModelAvailability(
            model_id="failing-model",
            provider="some-provider",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,  # Even if status is available
            last_checked=datetime.now(UTC),
            success_rate=0.5,
            response_time_ms=1000.0,
            error_count=10,
            circuit_breaker_state=CircuitBreakerState.OPEN,  # Circuit is open
            fallback_models=[],
        )
        service.availability_cache["openrouter:failing-model"] = availability

        result = service.is_model_available("failing-model", "openrouter")
        assert result is False, "Models with open circuit breaker should be unavailable"

    def test_model_in_maintenance_returns_unavailable(self):
        """Test that models in maintenance mode return False"""
        from src.services.model_availability import (
            AvailabilityStatus,
            CircuitBreakerState,
            ModelAvailability,
            ModelAvailabilityService,
        )

        service = ModelAvailabilityService()

        # Add a model in maintenance
        future_time = datetime.now(UTC) + timedelta(hours=2)
        availability = ModelAvailability(
            model_id="maintained-model",
            provider="some-provider",
            gateway="openrouter",
            status=AvailabilityStatus.MAINTENANCE,
            last_checked=datetime.now(UTC),
            success_rate=1.0,
            response_time_ms=100.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
            maintenance_until=future_time,
        )
        service.availability_cache["openrouter:maintained-model"] = availability

        result = service.is_model_available("maintained-model", "openrouter")
        assert result is False, "Models in maintenance should be unavailable"

    def test_unavailable_status_returns_unavailable(self):
        """Test that models with UNAVAILABLE status return False"""
        from src.services.model_availability import (
            AvailabilityStatus,
            CircuitBreakerState,
            ModelAvailability,
            ModelAvailabilityService,
        )

        service = ModelAvailabilityService()

        availability = ModelAvailability(
            model_id="broken-model",
            provider="some-provider",
            gateway="openrouter",
            status=AvailabilityStatus.UNAVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.0,
            response_time_ms=None,
            error_count=50,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )
        service.availability_cache["openrouter:broken-model"] = availability

        result = service.is_model_available("broken-model", "openrouter")
        assert result is False, "Models with UNAVAILABLE status should be unavailable"
