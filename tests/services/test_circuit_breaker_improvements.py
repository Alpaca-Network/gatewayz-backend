"""
Test Circuit Breaker Improvements - Issue #1089

Tests for the circuit breaker improvements implemented to fix:
- HALF_OPEN state immediately reopening on first failure
- Poor error messages for circuit breaker rejections
- Lack of consecutive opens tracking

Related: https://github.com/Alpaca-Network/gatewayz-backend/issues/1089
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

from src.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
    get_circuit_breaker,
)


class TestCircuitBreakerHalfOpenBehavior:
    """Test improvements to HALF_OPEN state recovery"""

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_half_open_allows_multiple_failures_before_reopening(self, mock_redis):
        """Test that HALF_OPEN state allows 2 failures before reopening"""
        # Mock Redis to avoid stale state
        mock_redis.return_value = None

        def failing_func():
            raise Exception("fail")

        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=1,
            timeout_seconds=1,
            half_open_max_failures=2,  # NEW: Allow 2 failures
        )
        breaker = CircuitBreaker("test-provider", config)

        # Force circuit to OPEN state
        for _ in range(3):
            try:
                breaker.call(failing_func)
            except Exception:
                pass

        assert breaker._state == CircuitState.OPEN, "Circuit should be OPEN after 3 failures"

        # Wait for timeout to transition to HALF_OPEN
        time.sleep(1.1)

        # First failure in HALF_OPEN should NOT reopen circuit
        try:
            breaker.call(failing_func)
        except Exception:
            pass

        assert breaker._state == CircuitState.HALF_OPEN, "Circuit should still be HALF_OPEN after 1 failure"

        # Second failure should reopen circuit
        try:
            breaker.call(failing_func)
        except Exception:
            pass

        assert breaker._state == CircuitState.OPEN, "Circuit should be OPEN after 2 failures in HALF_OPEN"

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_half_open_closes_after_success(self, mock_redis):
        """Test that HALF_OPEN transitions to CLOSED after success_threshold successes"""
        # Mock Redis to avoid stale state
        mock_redis.return_value = None

        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=1,  # Only need 1 success
            timeout_seconds=1,
            half_open_max_failures=2,
        )
        breaker = CircuitBreaker("test-provider", config)

        # Force circuit to OPEN
        for _ in range(3):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        assert breaker._state == CircuitState.OPEN

        # Wait for timeout
        time.sleep(1.1)

        # One success should close the circuit
        result = breaker.call(lambda: "success")
        assert result == "success"
        assert breaker._state == CircuitState.CLOSED, "Circuit should be CLOSED after 1 success"


class TestConsecutiveOpensTracking:
    """Test tracking of consecutive circuit opens"""

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_consecutive_opens_increments_on_failed_recovery(self, mock_redis):
        """Test that consecutive_opens increments when recovery fails (circuit reopens from HALF_OPEN)"""
        # Mock Redis to avoid stale state
        mock_redis.return_value = None

        def failing_func():
            raise Exception("fail")

        config = CircuitBreakerConfig(
            failure_threshold=2,
            success_threshold=1,
            timeout_seconds=1,
            half_open_max_failures=1,  # Reopen immediately on first failure
        )
        breaker = CircuitBreaker("test-provider", config)

        assert breaker._consecutive_opens == 0, "Should start at 0"

        # First open
        for _ in range(2):
            try:
                breaker.call(failing_func)
            except Exception:
                pass

        assert breaker._state == CircuitState.OPEN
        assert breaker._consecutive_opens == 1, "Should be 1 after first open"

        # Wait for HALF_OPEN, then fail recovery
        time.sleep(1.1)
        try:
            breaker.call(failing_func)
        except Exception:
            pass

        assert breaker._state == CircuitState.OPEN, "Should reopen after failed recovery"
        assert breaker._consecutive_opens == 2, "Should be 2 after failed recovery"

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_consecutive_opens_resets_on_successful_recovery(self, mock_redis):
        """Test that consecutive_opens resets to 0 after successful recovery"""
        # Mock Redis to avoid stale state
        mock_redis.return_value = None

        config = CircuitBreakerConfig(
            failure_threshold=2,
            success_threshold=1,  # Only need 1 success to close
            timeout_seconds=1,
        )
        breaker = CircuitBreaker("test-provider", config)

        # Open circuit
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        assert breaker._consecutive_opens == 1

        # Recover
        time.sleep(1.1)
        breaker.call(lambda: "success")

        assert breaker._state == CircuitState.CLOSED
        assert breaker._consecutive_opens == 0, "Should reset to 0 after recovery"


class TestCircuitBreakerErrorHandling:
    """Test CircuitBreakerError exception and error responses"""

    def test_circuit_breaker_error_raised_when_open(self):
        """Test that CircuitBreakerError is raised when circuit is OPEN"""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            timeout_seconds=60,  # Long timeout so circuit stays open
        )
        breaker = CircuitBreaker("openrouter", config)

        # Open the circuit
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        assert breaker._state == CircuitState.OPEN

        # Next call should raise CircuitBreakerError
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.call(lambda: "should not execute")

        error = exc_info.value
        assert error.provider == "openrouter"
        assert error.state == CircuitState.OPEN
        assert "Circuit breaker is OPEN" in error.message

    def test_circuit_breaker_error_contains_retry_info(self):
        """Test that CircuitBreakerError message contains retry information"""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            timeout_seconds=60,
        )
        breaker = CircuitBreaker("openrouter", config)

        # Open the circuit
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        # Verify error message
        with pytest.raises(CircuitBreakerError) as exc_info:
            breaker.call(lambda: "test")

        assert "60s" in exc_info.value.message or "60" in exc_info.value.message


class TestDetailedErrorFactory:
    """Test the new provider_unavailable error factory method"""

    def test_provider_unavailable_creates_503_error(self):
        """Test that provider_unavailable returns 503 status"""
        from src.utils.error_factory import DetailedErrorFactory

        error_response = DetailedErrorFactory.provider_unavailable(
            provider="openrouter",
            model="xiaomi/mimo-v2-flash:free",
            retry_after=60,
            circuit_breaker_state="open",
            request_id="test-123",
        )

        assert error_response.error.status == 503
        assert error_response.error.request_id == "test-123"

    def test_provider_unavailable_contains_helpful_info(self):
        """Test that error contains helpful information for users"""
        from src.utils.error_factory import DetailedErrorFactory

        error_response = DetailedErrorFactory.provider_unavailable(
            provider="openrouter",
            model="xiaomi/mimo-v2-flash:free",
            retry_after=60,
            circuit_breaker_state="open",
        )

        error = error_response.error

        # Check message contains provider and model
        assert "openrouter" in error.message.lower()
        assert "xiaomi/mimo-v2-flash:free" in error.message.lower()

        # Check detail explains circuit breaker
        assert "circuit breaker" in error.detail.lower()
        assert "open" in error.detail.lower()

        # Check suggestions exist
        assert len(error.suggestions) > 0
        assert any("60" in str(s) for s in error.suggestions)

        # Check context has retry_after
        assert error.context.retry_after == 60
        assert error.context.provider == "openrouter"


class TestOpenRouterCircuitConfig:
    """Test the optimized OpenRouter circuit breaker configuration"""

    def test_openrouter_config_has_optimized_values(self):
        """Test that OPENROUTER_CIRCUIT_CONFIG has the optimized parameters"""
        from src.services.openrouter_client import OPENROUTER_CIRCUIT_CONFIG

        # Check optimized values
        assert OPENROUTER_CIRCUIT_CONFIG.success_threshold == 1, "Should only need 1 success to recover"
        assert OPENROUTER_CIRCUIT_CONFIG.half_open_max_failures == 2, "Should allow 2 failures in HALF_OPEN"

        # Check other important values
        assert OPENROUTER_CIRCUIT_CONFIG.failure_threshold == 5
        assert OPENROUTER_CIRCUIT_CONFIG.timeout_seconds == 60


class TestRedisStatePersistence:
    """Test Redis persistence for distributed deployments"""

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_consecutive_opens_persisted_to_redis(self, mock_redis):
        """Test that consecutive_opens is saved to Redis"""
        mock_redis_client = MagicMock()
        mock_redis.return_value = mock_redis_client

        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker("test-provider", config)

        # Open the circuit
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        # Verify consecutive_opens was saved to Redis
        mock_redis_client.pipeline.assert_called()
        pipeline = mock_redis_client.pipeline.return_value

        # Check that setex was called with consecutive_opens key
        calls = pipeline.setex.call_args_list
        consecutive_opens_saved = any(
            "consecutive_opens" in str(call) for call in calls
        )
        assert consecutive_opens_saved, "consecutive_opens should be saved to Redis"

    @patch("src.services.circuit_breaker.get_redis_client")
    def test_consecutive_opens_loaded_from_redis(self, mock_redis):
        """Test that consecutive_opens is loaded from Redis"""
        mock_redis_client = MagicMock()
        mock_redis_client.get.side_effect = lambda key: "2" if "consecutive_opens" in key else None
        mock_redis.return_value = mock_redis_client

        breaker = CircuitBreaker("test-provider")
        breaker._load_state_from_redis()

        assert breaker._consecutive_opens == 2, "Should load consecutive_opens from Redis"


class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker with chat handler"""

    @patch("src.handlers.chat_handler.make_openrouter_request_openai")
    def test_chat_handler_catches_circuit_breaker_error(self, mock_openrouter):
        """Test that chat handler catches CircuitBreakerError and returns 503"""
        from src.handlers.chat_handler import ChatInferenceHandler
        from fastapi import HTTPException

        # Simulate circuit breaker error
        mock_openrouter.side_effect = CircuitBreakerError(
            provider="openrouter",
            state=CircuitState.OPEN,
        )

        handler = ChatInferenceHandler(api_key="test-key")

        # Call should catch CircuitBreakerError and raise HTTPException with 503
        with pytest.raises(HTTPException) as exc_info:
            handler._call_provider(
                provider_name="openrouter",
                model_id="test-model",
                messages=[{"role": "user", "content": "test"}],
            )

        # Verify it's a 503 error
        assert exc_info.value.status_code == 503

        # Verify detail contains helpful info
        detail = exc_info.value.detail
        assert "error" in detail
        assert detail["error"]["status"] == 503


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
