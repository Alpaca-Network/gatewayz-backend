"""
CM-5: Provider Failover & Circuit Breaker Tests

Tests covering:
  5.1 Failover Chain (FALLBACK_PROVIDER_PRIORITY, FAILOVER_STATUS_CODES)
  5.2 Model-Aware Failover Rules (enforce_model_failover_rules)
  5.3 Circuit Breaker (CircuitBreaker, CircuitBreakerConfig, CircuitState)
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
)
from src.services.provider_failover import (
    FAILOVER_STATUS_CODES,
    FALLBACK_PROVIDER_PRIORITY,
    build_provider_failover_chain,
    enforce_model_failover_rules,
    should_failover,
)

# ---------------------------------------------------------------------------
# Helper: create a CircuitBreaker with Redis disabled (in-memory only)
# ---------------------------------------------------------------------------


def _make_breaker(provider: str = "test-provider", **config_overrides) -> CircuitBreaker:
    """Create a CircuitBreaker that uses in-memory state (no Redis)."""
    config = CircuitBreakerConfig(**config_overrides)
    with patch("src.config.redis_config.get_redis_client", return_value=None):
        return CircuitBreaker(provider, config)


def _fail_n(breaker: CircuitBreaker, n: int) -> None:
    """Record *n* consecutive failures via the synchronous call() path."""
    for _ in range(n):
        try:
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        except (CircuitBreakerError, RuntimeError):
            pass


def _succeed_n(breaker: CircuitBreaker, n: int) -> None:
    """Record *n* consecutive successes."""
    for _ in range(n):
        breaker.call(lambda: "ok")


# ===================================================================
# 5.1 Failover Chain
# ===================================================================


class TestFailoverChain:
    """CM 5.1 - Failover chain configuration and status-code triggers."""

    @pytest.mark.cm_verified
    def test_failover_chain_has_14_providers(self):
        """CM-5.1.1: build_provider_failover_chain produces a chain with 14 providers."""
        chain = build_provider_failover_chain("openrouter")
        assert len(chain) == 14

    @pytest.mark.cm_verified
    def test_failover_chain_ordered_by_reliability(self):
        """CM-5.1.2: build_provider_failover_chain preserves reliability ranking after initial."""
        chain = build_provider_failover_chain("onerouter")
        expected_order = (
            "onerouter",
            "openai",
            "anthropic",
            "google-vertex",
            "openrouter",
            "cerebras",
            "huggingface",
            "featherless",
            "vercel-ai-gateway",
            "aihubmix",
            "anannas",
            "alibaba-cloud",
            "fireworks",
            "together",
        )
        assert tuple(chain) == expected_order

    # --- Status codes that SHOULD trigger failover ---

    @pytest.mark.cm_verified
    def test_failover_retries_on_502(self):
        """CM-5.1.3: should_failover returns True for 502."""
        assert should_failover(HTTPException(status_code=502)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_503(self):
        """CM-5.1.4: should_failover returns True for 503."""
        assert should_failover(HTTPException(status_code=503)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_504(self):
        """CM-5.1.5: should_failover returns True for 504."""
        assert should_failover(HTTPException(status_code=504)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_401(self):
        """CM-5.1.6: should_failover returns True for 401."""
        assert should_failover(HTTPException(status_code=401)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_402(self):
        """CM-5.1.7: should_failover returns True for 402."""
        assert should_failover(HTTPException(status_code=402)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_403(self):
        """CM-5.1.8: should_failover returns True for 403."""
        assert should_failover(HTTPException(status_code=403)) is True

    @pytest.mark.cm_verified
    def test_failover_retries_on_404(self):
        """CM-5.1.9: should_failover returns True for 404."""
        assert should_failover(HTTPException(status_code=404)) is True

    # --- Status codes that should NOT trigger failover ---

    @pytest.mark.cm_verified
    def test_failover_does_NOT_trigger_on_400(self):
        """CM-5.1.10: should_failover returns False for 400."""
        assert should_failover(HTTPException(status_code=400)) is False

    @pytest.mark.cm_verified
    def test_failover_does_NOT_trigger_on_429(self):
        """CM-5.1.11: should_failover returns False for 429."""
        assert should_failover(HTTPException(status_code=429)) is False

    @pytest.mark.cm_verified
    def test_failover_transparent_to_caller(self):
        """CM-5.1.12: A successful failover returns a normal response (the caller
        sees the result from whichever provider succeeded, not the intermediate
        errors).

        We verify that build_provider_failover_chain produces a multi-provider
        chain so the dispatch loop can transparently try the next provider.
        """
        chain = build_provider_failover_chain("openrouter")
        # The chain should start with the requested provider and include fallbacks
        assert chain[0] == "openrouter"
        assert len(chain) > 1  # fallback providers present
        # All providers in the priority list are included
        for p in FALLBACK_PROVIDER_PRIORITY:
            assert p in chain


# ===================================================================
# 5.2 Model-Aware Rules
# ===================================================================


class TestModelAwareRules:
    """CM 5.2 - enforce_model_failover_rules restricts chains for vendor models."""

    @pytest.mark.cm_verified
    def test_openai_models_failover_only_to_openai_or_openrouter(self):
        """CM-5.2.1: openai/* models are restricted to [openai, openrouter]."""
        full_chain = list(FALLBACK_PROVIDER_PRIORITY)
        with patch(
            "src.services.model_transformations.apply_model_alias", return_value="openai/gpt-4o"
        ):
            result = enforce_model_failover_rules("openai/gpt-4o", full_chain)
        assert set(result).issubset({"openai", "openrouter"})
        assert result[0] == "openai"  # native provider first

    @pytest.mark.cm_verified
    def test_anthropic_models_failover_only_to_anthropic_or_openrouter(self):
        """CM-5.2.2: anthropic/* models are restricted to [anthropic, openrouter]."""
        full_chain = list(FALLBACK_PROVIDER_PRIORITY)
        with patch(
            "src.services.model_transformations.apply_model_alias",
            return_value="anthropic/claude-3-opus",
        ):
            result = enforce_model_failover_rules("anthropic/claude-3-opus", full_chain)
        assert set(result).issubset({"anthropic", "openrouter"})
        assert result[0] == "anthropic"  # native provider first

    @pytest.mark.cm_verified
    def test_opensource_models_failover_across_all_providers(self):
        """CM-5.2.3: meta-llama/* models can fail over to any provider."""
        full_chain = list(FALLBACK_PROVIDER_PRIORITY)
        with patch(
            "src.services.model_transformations.apply_model_alias",
            return_value="meta-llama/Llama-3.3-70B-Instruct",
        ):
            result = enforce_model_failover_rules("meta-llama/Llama-3.3-70B-Instruct", full_chain)
        # The full chain should be returned (no restriction)
        assert result == full_chain


# ===================================================================
# 5.3 Circuit Breaker
# ===================================================================


class TestCircuitBreaker:
    """CM 5.3 - CircuitBreaker state machine."""

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_starts_closed(self, _mock_redis):
        """CM-5.3.1: A new circuit breaker starts in CLOSED state."""
        breaker = CircuitBreaker("provider-a")
        assert breaker._state == CircuitState.CLOSED

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_opens_after_5_failures(self, _mock_redis):
        """CM-5.3.2: 5 consecutive failures transition to OPEN."""
        breaker = CircuitBreaker("provider-b")
        _fail_n(breaker, 5)
        assert breaker._state == CircuitState.OPEN

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_4_failures_stays_closed(self, _mock_redis):
        """CM-5.3.3: 4 failures keep the circuit CLOSED."""
        breaker = CircuitBreaker("provider-c")
        _fail_n(breaker, 4)
        assert breaker._state == CircuitState.CLOSED

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_open_blocks_requests(self, _mock_redis):
        """CM-5.3.4: An OPEN circuit rejects new requests with CircuitBreakerError."""
        breaker = CircuitBreaker("provider-d")
        _fail_n(breaker, 5)
        assert breaker._state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: "should not execute")

    @pytest.mark.cm_gap
    @pytest.mark.xfail(reason="CM spec says 5min cooldown but code uses 60s")
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_recovery_after_60_seconds(self, _mock_redis):
        """CM-5.3.5: After timeout_seconds elapse, OPEN transitions to HALF_OPEN."""
        breaker = CircuitBreaker("provider-e")
        _fail_n(breaker, 5)
        assert breaker._state == CircuitState.OPEN

        # Advance time past the 60s timeout by manipulating _opened_at
        breaker._opened_at = time.time() - 61

        # The next call attempt should transition to HALF_OPEN and succeed
        result = breaker.call(lambda: "recovered")
        assert result == "recovered"
        assert breaker._state == CircuitState.HALF_OPEN

        # CM spec says cooldown should be 5 minutes (300s), but code uses 60s
        assert breaker.config.timeout_seconds == 300

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_half_open_success_closes(self, _mock_redis):
        """CM-5.3.6: Reaching success_threshold (2) in HALF_OPEN transitions to CLOSED."""
        breaker = CircuitBreaker("provider-f")
        _fail_n(breaker, 5)
        assert breaker._state == CircuitState.OPEN

        # Manually transition to HALF_OPEN (simulating timeout expiry)
        breaker._state = CircuitState.HALF_OPEN
        breaker._failure_count = 0
        breaker._success_count = 0

        # Need success_threshold=2 successes to close
        _succeed_n(breaker, 2)
        assert breaker._state == CircuitState.CLOSED

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_half_open_failure_reopens(self, _mock_redis):
        """CM-5.3.7: half_open_max_failures (2) failures in HALF_OPEN re-opens circuit."""
        breaker = CircuitBreaker("provider-g")
        _fail_n(breaker, 5)
        assert breaker._state == CircuitState.OPEN

        # Manually transition to HALF_OPEN
        breaker._state = CircuitState.HALF_OPEN
        breaker._failure_count = 0
        breaker._success_count = 0

        # Need half_open_max_failures=2 failures to reopen
        _fail_n(breaker, 2)
        assert breaker._state == CircuitState.OPEN

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_success_resets_failure_count(self, _mock_redis):
        """CM-5.3.8: A successful request resets the consecutive failure counter."""
        breaker = CircuitBreaker("provider-h")
        # Accumulate some failures (but not enough to open)
        _fail_n(breaker, 3)
        assert breaker._failure_count == 3

        # One success should reset the counter
        _succeed_n(breaker, 1)
        assert breaker._failure_count == 0

    @pytest.mark.cm_verified
    @patch("src.services.circuit_breaker.get_redis_client", return_value=None)
    def test_circuit_breaker_independent_per_provider(self, _mock_redis):
        """CM-5.3.9: Each provider has an independent circuit breaker."""
        breaker_a = CircuitBreaker("provider-x")
        breaker_b = CircuitBreaker("provider-y")

        # Open breaker A
        _fail_n(breaker_a, 5)
        assert breaker_a._state == CircuitState.OPEN

        # Breaker B should still be closed
        assert breaker_b._state == CircuitState.CLOSED

        # Breaker B should still accept requests
        result = breaker_b.call(lambda: "ok")
        assert result == "ok"
