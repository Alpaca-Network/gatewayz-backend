"""
Circuit Breaker Pattern Implementation

Prevents cascading failures by tracking provider health and automatically
skipping providers that are consistently failing.

Usage:
    breaker = get_provider_circuit_breaker()

    if breaker.should_skip("openrouter"):
        return []  # Skip this provider

    try:
        result = fetch_provider_models("openrouter")
        breaker.record_success("openrouter")
        return result
    except Exception as e:
        breaker.record_failure("openrouter")
        raise
"""

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderState:
    """Track health state for a single provider."""
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    consecutive_failures: int = 0
    is_open: bool = False  # True = circuit is open (skip provider)
    total_requests: int = 0
    total_failures: int = 0


class ProviderCircuitBreaker:
    """
    Circuit breaker for provider health management.

    States:
    - CLOSED: Normal operation, requests go through
    - OPEN: Provider is failing, skip requests for recovery_timeout
    - HALF_OPEN: After recovery_timeout, allow one request to test

    Configuration:
    - failure_threshold: Number of consecutive failures before opening circuit
    - recovery_timeout: Seconds to wait before trying again
    - success_threshold: Successes needed in half-open to close circuit
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 300.0,  # 5 minutes
        success_threshold: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._states: dict[str, ProviderState] = {}
        self._lock = Lock()

        logger.info(
            f"Circuit breaker initialized: "
            f"failure_threshold={failure_threshold}, "
            f"recovery_timeout={recovery_timeout}s"
        )

    def _get_state(self, provider: str) -> ProviderState:
        """Get or create provider state."""
        if provider not in self._states:
            self._states[provider] = ProviderState()
        return self._states[provider]

    def should_skip(self, provider: str) -> bool:
        """
        Check if provider should be skipped due to circuit being open.

        Returns:
            True if provider should be skipped (circuit open)
            False if provider should be tried (circuit closed or half-open)
        """
        with self._lock:
            state = self._get_state(provider)

            if not state.is_open:
                return False

            # Check if recovery timeout has passed
            elapsed = time.time() - state.last_failure_time
            if elapsed >= self.recovery_timeout:
                # Move to half-open state - allow one request
                logger.info(
                    f"Circuit breaker HALF-OPEN for {provider} "
                    f"(recovery timeout elapsed: {elapsed:.1f}s)"
                )
                return False

            # Circuit still open
            logger.debug(
                f"Circuit breaker OPEN for {provider} "
                f"(wait {self.recovery_timeout - elapsed:.1f}s more)"
            )
            return True

    def record_success(self, provider: str) -> None:
        """Record a successful request to provider."""
        with self._lock:
            state = self._get_state(provider)
            state.last_success_time = time.time()
            state.consecutive_failures = 0
            state.total_requests += 1

            if state.is_open:
                # Close the circuit on success (half-open → closed)
                state.is_open = False
                logger.info(f"Circuit breaker CLOSED for {provider} (success after recovery)")

    def record_failure(self, provider: str, error: str | None = None) -> None:
        """Record a failed request to provider."""
        with self._lock:
            state = self._get_state(provider)
            state.failure_count += 1
            state.consecutive_failures += 1
            state.last_failure_time = time.time()
            state.total_requests += 1
            state.total_failures += 1

            if state.consecutive_failures >= self.failure_threshold:
                if not state.is_open:
                    state.is_open = True
                    logger.warning(
                        f"Circuit breaker OPENED for {provider} "
                        f"(consecutive failures: {state.consecutive_failures}, "
                        f"error: {error or 'unknown'})"
                    )

    def reset(self, provider: str) -> None:
        """Manually reset circuit for a provider."""
        with self._lock:
            if provider in self._states:
                self._states[provider] = ProviderState()
                logger.info(f"Circuit breaker RESET for {provider}")

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            self._states.clear()
            logger.info("All circuit breakers RESET")

    def get_status(self, provider: str) -> dict[str, Any]:
        """Get status for a specific provider."""
        with self._lock:
            state = self._get_state(provider)
            return {
                "provider": provider,
                "is_open": state.is_open,
                "consecutive_failures": state.consecutive_failures,
                "total_failures": state.total_failures,
                "total_requests": state.total_requests,
                "last_failure_time": state.last_failure_time,
                "last_success_time": state.last_success_time,
                "failure_rate": (
                    state.total_failures / state.total_requests
                    if state.total_requests > 0 else 0
                ),
            }

    def get_all_status(self) -> dict[str, dict[str, Any]]:
        """Get status for all tracked providers."""
        with self._lock:
            return {
                provider: self.get_status(provider)
                for provider in self._states
            }

    def get_open_circuits(self) -> list[str]:
        """Get list of providers with open circuits."""
        with self._lock:
            return [
                provider for provider, state in self._states.items()
                if state.is_open
            ]


# Global circuit breaker instance
_circuit_breaker: ProviderCircuitBreaker | None = None


def get_provider_circuit_breaker() -> ProviderCircuitBreaker:
    """Get the global provider circuit breaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = ProviderCircuitBreaker(
            failure_threshold=3,  # Open after 3 consecutive failures
            recovery_timeout=300.0,  # Wait 5 minutes before retry
            success_threshold=1,  # Close after 1 success
        )
    return _circuit_breaker
