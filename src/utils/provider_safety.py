"""
Provider Safety Utilities

Provides retry logic, circuit breakers, and defensive patterns for external provider calls.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):  # noqa: UP042
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class ProviderError(Exception):
    """Base exception for provider errors."""

    pass


class ProviderTimeoutError(ProviderError):
    """Raised when provider request times out."""

    pass


class ProviderRateLimitError(ProviderError):
    """Raised when provider rate limit exceeded."""

    pass


class ProviderUnavailableError(ProviderError):
    """Raised when provider is unavailable."""

    pass


class CircuitBreaker:
    """
    Circuit breaker pattern for external API calls.

    Prevents cascading failures by tracking errors and opening the circuit
    when error threshold is reached.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Name of the circuit (for logging)
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            half_open_max_calls: Number of test calls in HALF_OPEN state
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.half_open_calls = 0

    def call(self, func: Callable[[], T]) -> T:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute

        Returns:
            Function result

        Raises:
            ProviderUnavailableError: If circuit is OPEN
        """
        # Check if circuit is OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"Circuit breaker {self.name}: Attempting recovery (HALF_OPEN)")
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                time_until_retry = (
                    self.recovery_timeout
                    - (time.time() - self.last_failure_time)
                    if self.last_failure_time
                    else 0
                )
                raise ProviderUnavailableError(
                    f"Circuit breaker {self.name} is OPEN. "
                    f"Retry in {time_until_retry:.1f}s"
                )

        # Execute function
        try:
            result = func()
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if not self.last_failure_time:
            return True
        return (time.time() - self.last_failure_time) >= self.recovery_timeout

    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                logger.info(f"Circuit breaker {self.name}: Recovery successful (CLOSED)")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.half_open_calls = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            logger.warning(
                f"Circuit breaker {self.name}: Recovery failed, reopening circuit"
            )
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            logger.error(
                f"Circuit breaker {self.name}: Failure threshold reached, "
                f"opening circuit ({self.failure_count} failures)"
            )
            self.state = CircuitState.OPEN


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 0.5,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    retry_on: tuple = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    ),
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        retry_on: Tuple of exception types to retry on

    Example:
        >>> @retry_with_backoff(max_retries=3)
        >>> def make_api_call():
        ...     return requests.get("https://api.example.com")
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e

                    if attempt < max_retries:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        time.sleep(delay)
                        delay = min(delay * exponential_base, max_delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries + 1} attempts: {e}"
                        )

            # All retries exhausted
            if last_exception is not None:
                raise last_exception
            raise RuntimeError(
                f"{func.__name__} failed after {max_retries + 1} attempts, "
                "but no exception was captured to re-raise."
            )

        return wrapper

    return decorator


async def retry_async_with_backoff(  # noqa: UP047
    func: Callable[..., T],
    max_retries: int = 3,
    initial_delay: float = 0.5,
    max_delay: float = 10.0,
    exponential_base: float = 2.0,
    retry_on: tuple = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    ),
) -> T:
    """
    Async retry with exponential backoff.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        retry_on: Tuple of exception types to retry on

    Returns:
        Function result

    Example:
        >>> async def fetch_data():
        ...     async with httpx.AsyncClient() as client:
        ...         return await client.get("https://api.example.com")
        >>> result = await retry_async_with_backoff(fetch_data)
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except retry_on as e:
            last_exception = e

            if attempt < max_retries:
                logger.warning(
                    f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * exponential_base, max_delay)
            else:
                logger.error(
                    f"{func.__name__} failed after {max_retries + 1} attempts: {e}"
                )

    # All retries exhausted
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(
        f"{func.__name__} failed after {max_retries + 1} attempts, "
        "but no exception was captured to re-raise."
    )


def safe_provider_call(  # noqa: UP047
    func: Callable[[], T],
    provider_name: str,
    timeout: float = 30.0,
    circuit_breaker: CircuitBreaker | None = None,
) -> T:
    """
    Safely execute provider API call with timeout and circuit breaker.

    Args:
        func: Function to execute
        provider_name: Name of provider (for logging)
        timeout: Timeout in seconds
        circuit_breaker: Optional circuit breaker instance

    Returns:
        Function result

    Raises:
        ProviderTimeoutError: If call times out
        ProviderUnavailableError: If circuit breaker is open

    Example:
        >>> cb = CircuitBreaker("openrouter")
        >>> result = safe_provider_call(
        ...     lambda: client.chat.completions.create(...),
        ...     "OpenRouter",
        ...     circuit_breaker=cb
        ... )
    """
    def execute_with_timeout():
        """Execute function with timeout enforcement."""
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                logger.error(f"{provider_name} call timed out after {timeout}s")
                raise ProviderTimeoutError(f"{provider_name} call timed out after {timeout}s")
            except Exception as e:
                logger.error(f"{provider_name} call failed: {e}")
                raise

    # Wrap in circuit breaker if provided
    if circuit_breaker:
        try:
            return circuit_breaker.call(execute_with_timeout)
        except Exception as e:
            logger.error(f"{provider_name} call failed through circuit breaker: {e}")
            raise

    # Direct call without circuit breaker
    return execute_with_timeout()


def validate_provider_response(
    response: Any,
    required_fields: list[str],
    provider_name: str,
) -> dict[str, Any]:
    """
    Validate provider response structure.

    Args:
        response: Response object to validate
        required_fields: List of required field names
        provider_name: Provider name for error messages

    Returns:
        Response as dictionary

    Raises:
        ProviderError: If response structure invalid

    Example:
        >>> response = openrouter_client.chat.completions.create(...)
        >>> validated = validate_provider_response(
        ...     response,
        ...     ["choices", "usage"],
        ...     "OpenRouter"
        ... )
    """
    if response is None:
        raise ProviderError(f"{provider_name}: Response is None")

    # Convert to dict - prioritize model_dump for Pydantic models
    if hasattr(response, "model_dump") and callable(response.model_dump):
        response_dict = response.model_dump()
    elif isinstance(response, dict):
        response_dict = response
    elif hasattr(response, "__dict__"):
        response_dict = response.__dict__
    else:
        raise ProviderError(
            f"{provider_name}: Cannot convert response to dict (type: {type(response)})"
        )

    # Check required fields
    missing_fields = [field for field in required_fields if field not in response_dict]
    if missing_fields:
        raise ProviderError(
            f"{provider_name}: Response missing required fields: {missing_fields}. "
            f"Available fields: {list(response_dict.keys())}"
        )

    return response_dict


def safe_get_choices(
    response: Any,
    provider_name: str,
    min_choices: int = 1,
) -> list[Any]:
    """
    Safely extract choices from provider response.

    Args:
        response: Provider response object
        provider_name: Provider name for error messages
        min_choices: Minimum number of choices required

    Returns:
        List of choices

    Raises:
        ProviderError: If choices invalid or insufficient

    Example:
        >>> choices = safe_get_choices(response, "OpenRouter")
    """
    if not hasattr(response, "choices"):
        raise ProviderError(f"{provider_name}: Response has no 'choices' attribute")

    choices = response.choices

    if not isinstance(choices, list):
        raise ProviderError(
            f"{provider_name}: choices is not a list (type: {type(choices)})"
        )

    if len(choices) < min_choices:
        raise ProviderError(
            f"{provider_name}: Expected at least {min_choices} choice(s), "
            f"got {len(choices)}"
        )

    return choices


def safe_get_usage(
    response: Any,
    provider_name: str,
) -> dict[str, int]:
    """
    Safely extract usage information from provider response.

    Args:
        response: Provider response object
        provider_name: Provider name for error messages

    Returns:
        Usage dictionary with prompt_tokens, completion_tokens, total_tokens

    Example:
        >>> usage = safe_get_usage(response, "OpenRouter")
        >>> print(usage["total_tokens"])
    """
    if not hasattr(response, "usage"):
        logger.warning(f"{provider_name}: Response has no usage information")
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    usage = response.usage

    if usage is None:
        logger.warning(f"{provider_name}: usage is None")
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    # Extract usage fields safely
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
        "completion_tokens": getattr(usage, "completion_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }
