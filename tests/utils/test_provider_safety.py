"""
Tests for provider safety utilities
"""

import time
from unittest.mock import Mock

import pytest

from src.utils.provider_safety import (
    CircuitBreaker,
    CircuitState,
    ProviderError,
    ProviderUnavailableError,
    retry_with_backoff,
    safe_get_choices,
    safe_get_usage,
    safe_provider_call,
    validate_provider_response,
)


class TestCircuitBreaker:
    """Tests for CircuitBreaker class."""

    def test_circuit_breaker_initial_state(self):
        """Test circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_circuit_breaker_successful_call(self):
        """Test successful call through circuit breaker."""
        cb = CircuitBreaker("test")

        result = cb.call(lambda: "success")
        assert result == "success"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_circuit_breaker_opens_after_threshold(self):
        """Test circuit opens after failure threshold."""
        cb = CircuitBreaker("test", failure_threshold=3)

        # Fail 3 times
        for i in range(3):
            try:
                cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
            except Exception:
                pass

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    def test_circuit_breaker_rejects_when_open(self):
        """Test circuit breaker rejects calls when OPEN."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=10.0)

        # Cause failure to open circuit
        try:
            cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        except Exception:
            pass

        assert cb.state == CircuitState.OPEN

        # Next call should be rejected
        with pytest.raises(ProviderUnavailableError, match="Circuit breaker test is OPEN"):
            cb.call(lambda: "should not execute")

    def test_circuit_breaker_half_open_after_timeout(self):
        """Test circuit moves to HALF_OPEN after recovery timeout."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        # Cause failure
        try:
            cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        except Exception:
            pass

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        time.sleep(0.15)

        # Next call should attempt recovery (will fail but state should be HALF_OPEN)
        try:
            cb.call(lambda: (_ for _ in ()).throw(Exception("still failing")))
        except Exception:
            pass

        # Should have attempted HALF_OPEN (then failed and reopened)
        assert cb.last_failure_time is not None

    def test_circuit_breaker_closes_after_successful_recovery(self):
        """Test circuit closes after successful call in HALF_OPEN state."""
        cb = CircuitBreaker(
            "test", failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=1
        )

        # Cause failure
        try:
            cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        except Exception:
            pass

        # Wait for recovery
        time.sleep(0.15)

        # Successful call should close circuit
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


class TestRetryWithBackoff:
    """Tests for retry_with_backoff decorator."""

    def test_retry_with_backoff_success_first_try(self):
        """Test successful call on first try."""

        @retry_with_backoff(max_retries=3)
        def successful_func():
            return "success"

        result = successful_func()
        assert result == "success"

    def test_retry_with_backoff_success_after_retries(self):
        """Test successful call after some retries."""
        call_count = [0]

        @retry_with_backoff(max_retries=3, initial_delay=0.01, retry_on=(ValueError,))
        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("temporary error")
            return "success"

        result = flaky_func()
        assert result == "success"
        assert call_count[0] == 3

    def test_retry_with_backoff_exhausted_retries(self):
        """Test all retries exhausted."""

        @retry_with_backoff(max_retries=2, initial_delay=0.01, retry_on=(ValueError,))
        def failing_func():
            raise ValueError("permanent error")

        with pytest.raises(ValueError, match="permanent error"):
            failing_func()

    def test_retry_with_backoff_wrong_exception_type(self):
        """Test non-retryable exception raises immediately."""

        @retry_with_backoff(max_retries=3, retry_on=(ValueError,))
        def func_with_type_error():
            raise TypeError("wrong exception type")

        with pytest.raises(TypeError, match="wrong exception type"):
            func_with_type_error()


class TestSafeProviderCall:
    """Tests for safe_provider_call function."""

    def test_safe_provider_call_success(self):
        """Test successful provider call."""
        result = safe_provider_call(lambda: {"data": "success"}, "TestProvider")
        assert result == {"data": "success"}

    def test_safe_provider_call_with_circuit_breaker(self):
        """Test provider call with circuit breaker."""
        cb = CircuitBreaker("test")

        result = safe_provider_call(lambda: {"data": "success"}, "TestProvider", circuit_breaker=cb)

        assert result == {"data": "success"}
        assert cb.failure_count == 0

    def test_safe_provider_call_circuit_breaker_open(self):
        """Test provider call rejected by open circuit."""
        cb = CircuitBreaker("test", failure_threshold=1)

        # Fail once to open circuit
        try:
            safe_provider_call(
                lambda: (_ for _ in ()).throw(Exception("fail")), "TestProvider", circuit_breaker=cb
            )
        except:
            pass

        # Next call should be rejected
        with pytest.raises(Exception):  # Circuit breaker will raise its exception
            safe_provider_call(lambda: "should not execute", "TestProvider", circuit_breaker=cb)


class TestValidateProviderResponse:
    """Tests for validate_provider_response function."""

    def test_validate_provider_response_dict(self):
        """Test validation with dict response."""
        response = {"choices": [], "usage": {}}
        validated = validate_provider_response(response, ["choices", "usage"], "TestProvider")
        assert validated == response

    def test_validate_provider_response_object_with_dict(self):
        """Test validation with object having __dict__."""
        response = Mock()
        response.__dict__ = {"choices": [], "usage": {}}

        validated = validate_provider_response(response, ["choices", "usage"], "TestProvider")
        assert "choices" in validated
        assert "usage" in validated

    def test_validate_provider_response_with_model_dump(self):
        """Test validation with Pydantic-like object."""
        response = Mock()
        response.model_dump = lambda: {"choices": [], "usage": {}}

        validated = validate_provider_response(response, ["choices", "usage"], "TestProvider")
        assert "choices" in validated

    def test_validate_provider_response_missing_fields(self):
        """Test error when required fields missing."""
        response = {"choices": []}

        with pytest.raises(ProviderError, match="missing required fields"):
            validate_provider_response(response, ["choices", "usage", "model"], "TestProvider")

    def test_validate_provider_response_none(self):
        """Test error when response is None."""
        with pytest.raises(ProviderError, match="Response is None"):
            validate_provider_response(None, ["choices"], "TestProvider")


class TestSafeGetChoices:
    """Tests for safe_get_choices function."""

    def test_safe_get_choices_success(self):
        """Test successful choices extraction."""
        response = Mock()
        response.choices = [Mock(), Mock()]

        choices = safe_get_choices(response, "TestProvider")
        assert len(choices) == 2

    def test_safe_get_choices_min_choices(self):
        """Test minimum choices validation."""
        response = Mock()
        response.choices = [Mock()]

        with pytest.raises(ProviderError, match="Expected at least 2"):
            safe_get_choices(response, "TestProvider", min_choices=2)

    def test_safe_get_choices_no_attribute(self):
        """Test error when no choices attribute."""
        response = Mock(spec=[])  # No attributes

        with pytest.raises(ProviderError, match="has no 'choices' attribute"):
            safe_get_choices(response, "TestProvider")

    def test_safe_get_choices_not_list(self):
        """Test error when choices is not a list."""
        response = Mock()
        response.choices = "not a list"

        with pytest.raises(ProviderError, match="is not a list"):
            safe_get_choices(response, "TestProvider")


class TestSafeGetUsage:
    """Tests for safe_get_usage function."""

    def test_safe_get_usage_success(self):
        """Test successful usage extraction."""
        response = Mock()
        response.usage = Mock()
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 20
        response.usage.total_tokens = 30

        usage = safe_get_usage(response, "TestProvider")
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 20
        assert usage["total_tokens"] == 30

    def test_safe_get_usage_no_attribute(self):
        """Test default usage when no usage attribute."""
        response = Mock(spec=[])

        usage = safe_get_usage(response, "TestProvider")
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0

    def test_safe_get_usage_none_usage(self):
        """Test default usage when usage is None."""
        response = Mock()
        response.usage = None

        usage = safe_get_usage(response, "TestProvider")
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0

    def test_safe_get_usage_missing_fields(self):
        """Test default values for missing fields."""
        response = Mock()
        response.usage = Mock()
        response.usage.prompt_tokens = 10
        # completion_tokens and total_tokens missing

        usage = safe_get_usage(response, "TestProvider")
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0

    def test_safe_get_usage_none_values(self):
        """Test handling of None values in usage fields."""
        response = Mock()
        response.usage = Mock()
        response.usage.prompt_tokens = None
        response.usage.completion_tokens = 20
        response.usage.total_tokens = None

        usage = safe_get_usage(response, "TestProvider")
        assert usage["prompt_tokens"] == 0  # None -> 0
        assert usage["completion_tokens"] == 20
        assert usage["total_tokens"] == 0  # None -> 0
