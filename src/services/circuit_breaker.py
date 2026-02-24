"""
Circuit Breaker Pattern Implementation for Provider API Calls

This module implements the circuit breaker pattern to prevent cascading failures
when provider APIs are experiencing issues. The circuit breaker has three states:

1. CLOSED: Normal operation, requests pass through
2. OPEN: Provider is failing, requests fail fast without calling the provider
3. HALF_OPEN: Testing if provider has recovered, limited requests pass through

Features:
- Automatic state transitions based on failure/success rates
- Configurable thresholds and timeouts
- Per-provider circuit breakers with independent state
- Prometheus metrics for monitoring
- Thread-safe implementation
- Graceful degradation support

Architecture:
- Each provider gets its own circuit breaker instance
- State transitions happen automatically based on observed behavior
- Circuit breakers are stored in Redis for distributed deployment support
- Fallback to in-memory state if Redis unavailable

Related Issues: #1043, #1039
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import Any, Callable

from src.config.redis_config import get_redis_client
from src.services.prometheus_metrics import (
    circuit_breaker_current_state,
    circuit_breaker_failures,
    circuit_breaker_rejected_requests,
    circuit_breaker_state_transitions,
    circuit_breaker_successes,
)

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):  # noqa: UP042
    """Circuit breaker states"""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior"""

    # Failure threshold: number of consecutive failures to open circuit
    failure_threshold: int = 5

    # Success threshold: number of consecutive successes in HALF_OPEN to close circuit
    success_threshold: int = 2

    # Timeout: seconds to wait before transitioning from OPEN to HALF_OPEN
    timeout_seconds: int = 60

    # Time window for measuring failure rate (seconds)
    failure_window_seconds: int = 60

    # Failure rate threshold (0.0-1.0): if failure rate exceeds this, open circuit
    failure_rate_threshold: float = 0.5

    # Minimum number of requests before failure rate is calculated
    min_requests_for_rate: int = 10

    # Maximum failures allowed in HALF_OPEN before reopening circuit
    # Setting this > 1 prevents immediate reopening on first failure during recovery
    half_open_max_failures: int = 2


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open and rejects a request"""

    def __init__(self, provider: str, state: CircuitState, message: str | None = None):
        self.provider = provider
        self.state = state
        self.message = message or f"Circuit breaker is {state.value} for provider '{provider}'"
        super().__init__(self.message)


class CircuitBreaker:
    """
    Circuit breaker for a single provider.

    Thread-safe implementation with state stored in Redis for distributed deployments.
    Falls back to in-memory state if Redis unavailable.
    """

    def __init__(self, provider: str, config: CircuitBreakerConfig | None = None):
        self.provider = provider
        self.config = config or CircuitBreakerConfig()
        self._lock = Lock()

        # In-memory state (fallback)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._opened_at = 0.0
        self._consecutive_opens = 0  # Track consecutive circuit opens for exponential backoff

        # Rolling window for failure rate calculation
        self._recent_requests: list[tuple[float, bool]] = []  # (timestamp, success)

        logger.info(
            f"Initialized circuit breaker for provider '{provider}' with config: {self.config}"
        )

    def _get_redis_key(self, suffix: str) -> str:
        """Generate Redis key for circuit breaker state"""
        return f"circuit_breaker:{self.provider}:{suffix}"

    def _load_state_from_redis(self) -> bool:
        """Load circuit breaker state from Redis"""
        try:
            redis = get_redis_client()
            if not redis:
                return False

            state_str = redis.get(self._get_redis_key("state"))
            if state_str:
                # Redis client has decode_responses=True, so state_str is already a string
                self._state = CircuitState(state_str)

            failure_count = redis.get(self._get_redis_key("failure_count"))
            if failure_count:
                self._failure_count = int(failure_count)

            success_count = redis.get(self._get_redis_key("success_count"))
            if success_count:
                self._success_count = int(success_count)

            opened_at = redis.get(self._get_redis_key("opened_at"))
            if opened_at:
                self._opened_at = float(opened_at)

            consecutive_opens = redis.get(self._get_redis_key("consecutive_opens"))
            if consecutive_opens:
                self._consecutive_opens = int(consecutive_opens)

            return True
        except Exception as e:
            logger.warning(f"Failed to load circuit breaker state from Redis: {e}")
            return False

    def _save_state_to_redis(self) -> bool:
        """Save circuit breaker state to Redis"""
        try:
            redis = get_redis_client()
            if not redis:
                return False

            # Use a pipeline for atomic updates
            pipe = redis.pipeline()
            ttl = 3600  # Keep state for 1 hour

            pipe.setex(self._get_redis_key("state"), ttl, self._state.value)
            pipe.setex(self._get_redis_key("failure_count"), ttl, str(self._failure_count))
            pipe.setex(self._get_redis_key("success_count"), ttl, str(self._success_count))
            pipe.setex(self._get_redis_key("opened_at"), ttl, str(self._opened_at))
            pipe.setex(self._get_redis_key("consecutive_opens"), ttl, str(self._consecutive_opens))

            pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Failed to save circuit breaker state to Redis: {e}")
            return False

    def _calculate_failure_rate(self) -> tuple[float, int]:
        """Calculate failure rate over the recent time window"""
        now = time.time()
        cutoff = now - self.config.failure_window_seconds

        # Remove old requests
        self._recent_requests = [
            (ts, success) for ts, success in self._recent_requests if ts > cutoff
        ]

        if len(self._recent_requests) < self.config.min_requests_for_rate:
            return 0.0, len(self._recent_requests)

        total_requests = len(self._recent_requests)
        failures = sum(1 for _, success in self._recent_requests if not success)

        return failures / total_requests, total_requests

    def _transition_to(self, new_state: CircuitState, reason: str = "") -> None:
        """Transition to a new circuit breaker state"""
        old_state = self._state

        if old_state == new_state:
            return

        self._state = new_state

        # Reset counters on state transition
        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._consecutive_opens = 0  # Reset consecutive opens on successful recovery
        elif new_state == CircuitState.OPEN:
            self._opened_at = time.time()
            self._success_count = 0
            self._consecutive_opens += 1  # Increment consecutive opens
        elif new_state == CircuitState.HALF_OPEN:
            self._failure_count = 0
            self._success_count = 0

        # Save state to Redis
        self._save_state_to_redis()

        # Update metrics
        circuit_breaker_state_transitions.labels(
            provider=self.provider, from_state=old_state.value, to_state=new_state.value
        ).inc()

        circuit_breaker_current_state.labels(provider=self.provider, state=new_state.value).set(1)

        circuit_breaker_current_state.labels(provider=self.provider, state=old_state.value).set(0)

        log_msg = f"Circuit breaker for '{self.provider}' transitioned: {old_state.value} → {new_state.value}"
        if reason:
            log_msg += f" ({reason})"

        logger.warning(log_msg)

    def _check_should_attempt(self) -> bool:
        """Check if request should be attempted based on circuit state"""
        with self._lock:
            # Load state from Redis (for distributed deployments)
            self._load_state_from_redis()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                return True

            if self._state == CircuitState.OPEN:
                # Check if timeout has elapsed
                now = time.time()
                if now - self._opened_at >= self.config.timeout_seconds:
                    self._transition_to(
                        CircuitState.HALF_OPEN, f"timeout elapsed ({self.config.timeout_seconds}s)"
                    )
                    return True

                return False

            return False

    def _record_success(self) -> None:
        """Record a successful request"""
        with self._lock:
            now = time.time()
            self._recent_requests.append((now, True))
            self._failure_count = 0
            self._success_count += 1
            self._last_failure_time = 0.0

            circuit_breaker_successes.labels(provider=self.provider, state=self._state.value).inc()

            if self._state == CircuitState.HALF_OPEN:
                if self._success_count >= self.config.success_threshold:
                    self._transition_to(
                        CircuitState.CLOSED,
                        f"success threshold reached ({self.config.success_threshold} successes)",
                    )

            self._save_state_to_redis()

    def _record_failure(self) -> None:
        """Record a failed request"""
        with self._lock:
            now = time.time()
            self._recent_requests.append((now, False))
            self._failure_count += 1
            self._success_count = 0
            self._last_failure_time = now

            circuit_breaker_failures.labels(provider=self.provider, state=self._state.value).inc()

            if self._state == CircuitState.HALF_OPEN:
                # Allow multiple failures in HALF_OPEN before reopening
                # This prevents immediate reopening on transient failures during recovery
                if self._failure_count >= self.config.half_open_max_failures:
                    self._transition_to(
                        CircuitState.OPEN,
                        f"recovery test failed ({self._failure_count} failures in HALF_OPEN)",
                    )
            elif self._state == CircuitState.CLOSED:
                # Check consecutive failures
                if self._failure_count >= self.config.failure_threshold:
                    self._transition_to(
                        CircuitState.OPEN,
                        f"failure threshold reached ({self.config.failure_threshold} consecutive failures)",
                    )
                else:
                    # Check failure rate
                    failure_rate, total_requests = self._calculate_failure_rate()
                    if (
                        total_requests >= self.config.min_requests_for_rate
                        and failure_rate >= self.config.failure_rate_threshold
                    ):
                        self._transition_to(
                            CircuitState.OPEN,
                            f"failure rate threshold reached ({failure_rate:.1%} >= {self.config.failure_rate_threshold:.1%})",
                        )

            self._save_state_to_redis()

    def call(self, func: Callable[[], Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute a function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func execution

        Raises:
            CircuitBreakerError: If circuit is open and request is rejected
            Exception: Any exception raised by func
        """
        if not self._check_should_attempt():
            circuit_breaker_rejected_requests.labels(provider=self.provider).inc()

            raise CircuitBreakerError(
                provider=self.provider,
                state=self._state,
                message=f"Circuit breaker is OPEN for provider '{self.provider}'. "
                f"Provider will be retried in {self.config.timeout_seconds}s.",
            )

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    async def call_async(self, func: Callable[[], Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute an async function with circuit breaker protection.

        Args:
            func: Async function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func execution

        Raises:
            CircuitBreakerError: If circuit is open and request is rejected
            Exception: Any exception raised by func
        """
        if not self._check_should_attempt():
            circuit_breaker_rejected_requests.labels(provider=self.provider).inc()

            raise CircuitBreakerError(
                provider=self.provider,
                state=self._state,
                message=f"Circuit breaker is OPEN for provider '{self.provider}'. "
                f"Provider will be retried in {self.config.timeout_seconds}s.",
            )

        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    def get_state(self) -> dict[str, Any]:
        """Get current circuit breaker state for monitoring"""
        with self._lock:
            self._load_state_from_redis()
            failure_rate, total_requests = self._calculate_failure_rate()

            return {
                "provider": self.provider,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_rate": failure_rate,
                "recent_requests": total_requests,
                "opened_at": (
                    datetime.fromtimestamp(self._opened_at, tz=UTC).isoformat()
                    if self._opened_at
                    else None
                ),
                "seconds_until_retry": (
                    max(0, int(self.config.timeout_seconds - (time.time() - self._opened_at)))
                    if self._state == CircuitState.OPEN
                    else 0
                ),
            }

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state"""
        with self._lock:
            self._transition_to(CircuitState.CLOSED, "manual reset")
            self._failure_count = 0
            self._success_count = 0
            self._recent_requests = []
            logger.info(f"Circuit breaker for '{self.provider}' manually reset")


# Global registry of circuit breakers
_circuit_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_circuit_breaker(
    provider: str, config: CircuitBreakerConfig | None = None
) -> CircuitBreaker:
    """
    Get or create a circuit breaker for a provider.

    Args:
        provider: Provider name (e.g., 'openrouter', 'groq')
        config: Optional configuration (uses defaults if not provided)

    Returns:
        CircuitBreaker instance for the provider
    """
    with _registry_lock:
        if provider not in _circuit_breakers:
            _circuit_breakers[provider] = CircuitBreaker(provider, config)
        return _circuit_breakers[provider]


def get_all_circuit_breakers() -> dict[str, dict[str, Any]]:
    """Get state of all circuit breakers for monitoring"""
    with _registry_lock:
        return {provider: breaker.get_state() for provider, breaker in _circuit_breakers.items()}


def reset_circuit_breaker(provider: str) -> bool:
    """
    Manually reset a circuit breaker to CLOSED state.

    Args:
        provider: Provider name

    Returns:
        True if reset successful, False if provider not found
    """
    with _registry_lock:
        if provider in _circuit_breakers:
            _circuit_breakers[provider].reset()
            return True
        return False


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers to CLOSED state"""
    with _registry_lock:
        for breaker in _circuit_breakers.values():
            breaker.reset()
        logger.info(f"Reset {len(_circuit_breakers)} circuit breakers")
