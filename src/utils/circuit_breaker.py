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

import json
import logging
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "circuit_breaker"
_REDIS_TTL = 3600  # 1 hour — auto-expire stale entries


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
        self._lock = RLock()

        logger.info(
            f"Circuit breaker initialized: "
            f"failure_threshold={failure_threshold}, "
            f"recovery_timeout={recovery_timeout}s"
        )

    # ------------------------------------------------------------------
    # Redis persistence (optional — degrades gracefully when unavailable)
    # ------------------------------------------------------------------

    def _save_state_to_redis(self, provider: str) -> None:
        """Persist the current state for *provider* to Redis.

        Called whenever the circuit transitions state (open / half-open /
        closed). Silently skips if Redis is unavailable so the breaker
        continues to work in-memory only.
        """
        try:
            from src.config.redis_config import get_redis_client

            client = get_redis_client()
            if client is None:
                return
            state = self._states.get(provider)
            if state is None:
                return
            payload = json.dumps(
                {
                    "failure_count": state.failure_count,
                    "last_failure_time": state.last_failure_time,
                    "last_success_time": state.last_success_time,
                    "consecutive_failures": state.consecutive_failures,
                    "is_open": state.is_open,
                    "total_requests": state.total_requests,
                    "total_failures": state.total_failures,
                }
            )
            key = f"{_REDIS_KEY_PREFIX}:{provider}"
            client.setex(key, _REDIS_TTL, payload)
        except Exception as exc:
            logger.debug(f"circuit_breaker: failed to save state for {provider} to Redis: {exc}")

    def _restore_state_from_redis(self, provider: str) -> None:
        """Load persisted state for *provider* from Redis into memory.

        Called lazily from ``_get_state`` the first time a provider is
        accessed after a restart. If Redis is unavailable or holds no
        entry the provider starts with a fresh ``ProviderState``.
        """
        try:
            from src.config.redis_config import get_redis_client

            client = get_redis_client()
            if client is None:
                return
            key = f"{_REDIS_KEY_PREFIX}:{provider}"
            raw = client.get(key)
            if raw is None:
                return
            data = json.loads(raw)
            state = self._states[provider]  # already created by _get_state
            state.failure_count = int(data.get("failure_count", 0))
            state.last_failure_time = float(data.get("last_failure_time", 0.0))
            state.last_success_time = float(data.get("last_success_time", 0.0))
            state.consecutive_failures = int(data.get("consecutive_failures", 0))
            state.is_open = bool(data.get("is_open", False))
            state.total_requests = int(data.get("total_requests", 0))
            state.total_failures = int(data.get("total_failures", 0))
            if state.is_open:
                logger.info(f"Circuit breaker RESTORED OPEN state for {provider} from Redis")
        except Exception as exc:
            logger.debug(
                f"circuit_breaker: failed to restore state for {provider} from Redis: {exc}"
            )

    def _get_state(self, provider: str) -> ProviderState:
        """Get or create provider state, restoring from Redis on first access."""
        if provider not in self._states:
            self._states[provider] = ProviderState()
            self._restore_state_from_redis(provider)
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
                self._save_state_to_redis(provider)

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
                    self._save_state_to_redis(provider)

    def reset(self, provider: str) -> None:
        """Manually reset circuit for a provider."""
        with self._lock:
            if provider in self._states:
                self._states[provider] = ProviderState()
                logger.info(f"Circuit breaker RESET for {provider}")
                try:
                    from src.config.redis_config import get_redis_client

                    client = get_redis_client()
                    if client:
                        client.delete(f"{_REDIS_KEY_PREFIX}:{provider}")
                except Exception as exc:
                    logger.debug(
                        f"circuit_breaker: failed to delete Redis key for {provider}: {exc}"
                    )

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
                    state.total_failures / state.total_requests if state.total_requests > 0 else 0
                ),
            }

    def get_all_status(self) -> dict[str, dict[str, Any]]:
        """Get status for all tracked providers."""
        with self._lock:
            return {provider: self.get_status(provider) for provider in self._states}

    def get_open_circuits(self) -> list[str]:
        """Get list of providers with open circuits."""
        with self._lock:
            return [provider for provider, state in self._states.items() if state.is_open]


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
