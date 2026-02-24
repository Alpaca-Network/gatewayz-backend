"""
Comprehensive tests for Model Availability Service and Circuit Breaker

Tests cover:
- Circuit breaker state transitions
- Model availability checking
- Fallback model selection
- Availability summary generation
- Maintenance mode management
"""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"

from src.services.model_availability import (
    AvailabilityConfig,
    AvailabilityStatus,
    CircuitBreaker,
    CircuitBreakerState,
    ModelAvailability,
    ModelAvailabilityService,
    availability_service,
)


class TestCircuitBreakerStateTransitions:
    """Test circuit breaker state machine"""

    def test_initial_state_is_closed(self):
        """Test circuit breaker starts in CLOSED state"""
        cb = CircuitBreaker()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_can_execute_when_closed(self):
        """Test requests can execute when CLOSED"""
        cb = CircuitBreaker()
        assert cb.can_execute() is True

    def test_transitions_to_open_after_failures(self):
        """Test CLOSED -> OPEN after failure threshold"""
        cb = CircuitBreaker(failure_threshold=3)

        # Record failures up to threshold
        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN

    def test_cannot_execute_when_open(self):
        """Test requests blocked when OPEN"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=300)

        # Force OPEN state
        cb.record_failure()
        cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False

    def test_transitions_to_half_open_after_recovery_timeout(self):
        """Test OPEN -> HALF_OPEN after recovery timeout"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1)

        # Force OPEN state
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        # Mock time to simulate recovery timeout elapsed
        cb.last_failure_time = time.time() - 2

        # Should transition to HALF_OPEN
        assert cb.can_execute() is True
        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_half_open_to_closed_after_successes(self):
        """Test HALF_OPEN -> CLOSED after success threshold"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0, success_threshold=2)

        # Force OPEN state
        cb.record_failure()
        cb.record_failure()

        # Transition to HALF_OPEN
        cb.can_execute()
        assert cb.state == CircuitBreakerState.HALF_OPEN

        # Record successes
        cb.record_success()
        cb.record_success()

        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.failure_count == 0

    def test_half_open_to_open_on_failure(self):
        """Test HALF_OPEN -> OPEN on failure"""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)

        # Force OPEN state
        cb.record_failure()
        cb.record_failure()

        # Transition to HALF_OPEN
        cb.can_execute()
        assert cb.state == CircuitBreakerState.HALF_OPEN

        # Record failure
        cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN

    def test_success_decrements_failure_count_when_closed(self):
        """Test success reduces failure count in CLOSED state"""
        cb = CircuitBreaker(failure_threshold=5)

        # Record some failures (below threshold)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        # Record success
        cb.record_success()
        assert cb.failure_count == 1

        cb.record_success()
        assert cb.failure_count == 0

        # Don't go below 0
        cb.record_success()
        assert cb.failure_count == 0


class TestAvailabilityConfig:
    """Test availability configuration"""

    def test_default_config_values(self):
        """Test default configuration values"""
        config = AvailabilityConfig()

        assert config.check_interval == 60
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 300
        assert config.success_threshold == 3
        assert config.response_timeout == 30
        assert config.cache_ttl == 300

    def test_custom_config_values(self):
        """Test custom configuration values"""
        config = AvailabilityConfig(check_interval=30, failure_threshold=10, recovery_timeout=600)

        assert config.check_interval == 30
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 600


class TestModelAvailabilityDataclass:
    """Test ModelAvailability dataclass"""

    def test_create_model_availability(self):
        """Test creating ModelAvailability instance"""
        from datetime import timezone

        availability = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=2,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=["gpt-3.5-turbo"],
        )

        assert availability.model_id == "gpt-4"
        assert availability.status == AvailabilityStatus.AVAILABLE
        assert availability.success_rate == 0.95
        assert len(availability.fallback_models) == 1


class TestModelAvailabilityService:
    """Test ModelAvailabilityService functionality"""

    def test_service_initialization(self):
        """Test service initializes correctly"""
        service = ModelAvailabilityService()

        assert service.availability_cache == {}
        assert service.circuit_breakers == {}
        assert service.monitoring_active is False
        assert len(service.fallback_mappings) > 0

    def test_fallback_mappings_loaded(self):
        """Test fallback mappings are loaded correctly"""
        service = ModelAvailabilityService()

        # Check some known fallback mappings
        assert "gpt-4" in service.fallback_mappings
        assert "claude-3-opus" in service.fallback_mappings
        assert "llama-3-70b" in service.fallback_mappings

        # Check fallbacks are valid
        gpt4_fallbacks = service.fallback_mappings["gpt-4"]
        assert isinstance(gpt4_fallbacks, list)
        assert len(gpt4_fallbacks) > 0

    def test_get_fallback_models(self):
        """Test getting fallback models"""
        service = ModelAvailabilityService()

        fallbacks = service.get_fallback_models("gpt-4")
        assert isinstance(fallbacks, list)
        assert len(fallbacks) > 0

        # Non-existent model returns empty list
        fallbacks = service.get_fallback_models("non-existent-model")
        assert fallbacks == []

    def test_get_model_availability_not_found(self):
        """Test getting availability for unknown model"""
        service = ModelAvailabilityService()

        result = service.get_model_availability("unknown-model")
        assert result is None

    def test_get_model_availability_with_gateway(self):
        """Test getting availability with specific gateway"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add a model to cache
        availability = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )
        service.availability_cache["openrouter:gpt-4"] = availability

        # Should find with gateway
        result = service.get_model_availability("gpt-4", "openrouter")
        assert result is not None
        assert result.model_id == "gpt-4"

        # Should not find with different gateway
        result = service.get_model_availability("gpt-4", "other-gateway")
        assert result is None

    def test_get_available_models_empty(self):
        """Test getting available models when cache is empty"""
        service = ModelAvailabilityService()

        result = service.get_available_models()
        assert result == []

    def test_get_available_models_filtered(self):
        """Test getting available models with filters"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add models to cache
        models = [
            ModelAvailability(
                model_id="gpt-4",
                provider="openai",
                gateway="openrouter",
                status=AvailabilityStatus.AVAILABLE,
                last_checked=datetime.now(UTC),
                success_rate=0.95,
                response_time_ms=150.0,
                error_count=0,
                circuit_breaker_state=CircuitBreakerState.CLOSED,
                fallback_models=[],
            ),
            ModelAvailability(
                model_id="claude-3",
                provider="anthropic",
                gateway="openrouter",
                status=AvailabilityStatus.AVAILABLE,
                last_checked=datetime.now(UTC),
                success_rate=0.90,
                response_time_ms=200.0,
                error_count=0,
                circuit_breaker_state=CircuitBreakerState.CLOSED,
                fallback_models=[],
            ),
            ModelAvailability(
                model_id="llama-3",
                provider="meta",
                gateway="huggingface",
                status=AvailabilityStatus.UNAVAILABLE,
                last_checked=datetime.now(UTC),
                success_rate=0.50,
                response_time_ms=None,
                error_count=5,
                circuit_breaker_state=CircuitBreakerState.OPEN,
                fallback_models=[],
            ),
        ]

        for m in models:
            service.availability_cache[f"{m.gateway}:{m.model_id}"] = m

        # Get all available (should exclude unavailable)
        result = service.get_available_models()
        assert len(result) == 2

        # Filter by gateway
        result = service.get_available_models(gateway="huggingface")
        assert len(result) == 0  # llama-3 is unavailable

        # Filter by provider
        result = service.get_available_models(provider="openai")
        assert len(result) == 1
        assert result[0].model_id == "gpt-4"

    def test_is_model_available(self):
        """Test model availability check"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add available model
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )

        assert service.is_model_available("gpt-4", "openrouter") is True
        # Unknown models return True (optimistic default) - they are assumed available
        # until the circuit breaker detects actual failures
        assert service.is_model_available("unknown", "openrouter") is True

    def test_is_model_available_with_open_circuit(self):
        """Test availability check with open circuit breaker"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add model with OPEN circuit
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.OPEN,
            fallback_models=[],
        )

        # Should return False because circuit is OPEN
        assert service.is_model_available("gpt-4", "openrouter") is False

    def test_is_model_available_during_maintenance(self):
        """Test availability check during maintenance"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add model in maintenance
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
            maintenance_until=datetime.now(UTC) + timedelta(hours=1),
        )

        # Should return False because of maintenance
        assert service.is_model_available("gpt-4", "openrouter") is False

    def test_get_best_available_model_preferred_available(self):
        """Test best model selection when preferred is available"""
        from datetime import timezone

        service = ModelAvailabilityService()

        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )

        result = service.get_best_available_model("gpt-4", "openrouter")
        assert result == "gpt-4"

    def test_get_best_available_model_with_fallback(self):
        """Test best model selection falls back when preferred unavailable"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # gpt-4 unavailable
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.UNAVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.50,
            response_time_ms=None,
            error_count=10,
            circuit_breaker_state=CircuitBreakerState.OPEN,
            fallback_models=[],
        )

        # gpt-4-turbo also unavailable (first fallback)
        service.availability_cache["openrouter:gpt-4-turbo"] = ModelAvailability(
            model_id="gpt-4-turbo",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.UNAVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.50,
            response_time_ms=None,
            error_count=10,
            circuit_breaker_state=CircuitBreakerState.OPEN,
            fallback_models=[],
        )

        # gpt-3.5-turbo available (second fallback)
        service.availability_cache["openrouter:gpt-3.5-turbo"] = ModelAvailability(
            model_id="gpt-3.5-turbo",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=100.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )

        result = service.get_best_available_model("gpt-4", "openrouter")
        assert result == "gpt-3.5-turbo"

    def test_get_availability_summary_empty(self):
        """Test availability summary with empty cache"""
        service = ModelAvailabilityService()

        summary = service.get_availability_summary()

        assert summary["total_models"] == 0
        assert summary["available_models"] == 0
        assert summary["availability_percentage"] == 0

    def test_get_availability_summary(self):
        """Test availability summary with data"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add models
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )
        service.availability_cache["openrouter:gpt-3.5"] = ModelAvailability(
            model_id="gpt-3.5",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.UNAVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.50,
            response_time_ms=None,
            error_count=5,
            circuit_breaker_state=CircuitBreakerState.OPEN,
            fallback_models=[],
        )
        service.availability_cache["huggingface:llama"] = ModelAvailability(
            model_id="llama",
            provider="meta",
            gateway="huggingface",
            status=AvailabilityStatus.DEGRADED,
            last_checked=datetime.now(UTC),
            success_rate=0.75,
            response_time_ms=500.0,
            error_count=2,
            circuit_breaker_state=CircuitBreakerState.HALF_OPEN,
            fallback_models=[],
        )

        summary = service.get_availability_summary()

        assert summary["total_models"] == 3
        assert summary["available_models"] == 1
        assert summary["degraded_models"] == 1
        assert summary["unavailable_models"] == 1
        assert summary["availability_percentage"] == pytest.approx(33.33, rel=0.1)
        assert "openrouter" in summary["gateway_stats"]
        assert "huggingface" in summary["gateway_stats"]

    def test_set_maintenance_mode(self):
        """Test setting maintenance mode"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add model
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.AVAILABLE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
        )

        # Set maintenance
        maintenance_end = datetime.now(UTC) + timedelta(hours=2)
        service.set_maintenance_mode("gpt-4", "openrouter", maintenance_end)

        availability = service.availability_cache["openrouter:gpt-4"]
        assert availability.status == AvailabilityStatus.MAINTENANCE
        assert availability.maintenance_until == maintenance_end

    def test_clear_maintenance_mode(self):
        """Test clearing maintenance mode"""
        from datetime import timezone

        service = ModelAvailabilityService()

        # Add model in maintenance
        service.availability_cache["openrouter:gpt-4"] = ModelAvailability(
            model_id="gpt-4",
            provider="openai",
            gateway="openrouter",
            status=AvailabilityStatus.MAINTENANCE,
            last_checked=datetime.now(UTC),
            success_rate=0.95,
            response_time_ms=150.0,
            error_count=0,
            circuit_breaker_state=CircuitBreakerState.CLOSED,
            fallback_models=[],
            maintenance_until=datetime.now(UTC) + timedelta(hours=2),
        )

        # Clear maintenance
        service.clear_maintenance_mode("gpt-4", "openrouter")

        availability = service.availability_cache["openrouter:gpt-4"]
        assert availability.maintenance_until is None


class TestModelAvailabilityServiceAsync:
    """Test async functionality of ModelAvailabilityService"""

    @pytest.mark.asyncio
    async def test_start_monitoring(self):
        """Test starting availability monitoring"""
        service = ModelAvailabilityService()

        # Start monitoring
        await service.start_monitoring()
        assert service.monitoring_active is True

        # Stop monitoring
        await service.stop_monitoring()
        assert service.monitoring_active is False

    @pytest.mark.asyncio
    async def test_start_monitoring_idempotent(self):
        """Test starting monitoring multiple times is idempotent"""
        service = ModelAvailabilityService()

        await service.start_monitoring()
        await service.start_monitoring()  # Should not start again

        assert service.monitoring_active is True

        await service.stop_monitoring()

    @pytest.mark.asyncio
    async def test_stop_monitoring_when_not_started(self):
        """Test stopping monitoring when not started"""
        service = ModelAvailabilityService()

        # Should not raise
        await service.stop_monitoring()
        assert service.monitoring_active is False


class TestAvailabilityStatus:
    """Test AvailabilityStatus enum"""

    def test_available_status(self):
        """Test AVAILABLE status"""
        assert AvailabilityStatus.AVAILABLE.value == "available"

    def test_unavailable_status(self):
        """Test UNAVAILABLE status"""
        assert AvailabilityStatus.UNAVAILABLE.value == "unavailable"

    def test_degraded_status(self):
        """Test DEGRADED status"""
        assert AvailabilityStatus.DEGRADED.value == "degraded"

    def test_maintenance_status(self):
        """Test MAINTENANCE status"""
        assert AvailabilityStatus.MAINTENANCE.value == "maintenance"

    def test_unknown_status(self):
        """Test UNKNOWN status"""
        assert AvailabilityStatus.UNKNOWN.value == "unknown"


class TestGlobalAvailabilityService:
    """Test the global availability_service instance"""

    def test_global_service_exists(self):
        """Test global service is initialized"""
        assert availability_service is not None
        assert isinstance(availability_service, ModelAvailabilityService)

    def test_global_service_has_fallback_mappings(self):
        """Test global service has fallback mappings loaded"""
        assert len(availability_service.fallback_mappings) > 0
