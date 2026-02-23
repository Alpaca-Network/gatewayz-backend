"""
Provider Error Logging Utilities

Standardized error logging and context management for provider model fetching.
Provides consistent error classification, structured logging, and performance tracking.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


class ProviderErrorType(Enum):
    """Classification of provider errors for better debugging and monitoring"""

    API_TIMEOUT = "api_timeout"
    AUTH_FAILURE = "auth_failure"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    PARSING_ERROR = "parsing_error"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    DATABASE_ERROR = "database_error"
    CACHE_ERROR = "cache_error"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN = "unknown"


@dataclass
class ProviderFetchContext:
    """Context information for provider fetch operations"""

    provider_slug: str
    endpoint_url: str | None = None
    status_code: int | None = None
    models_fetched: int = 0
    models_expected: int | None = None
    retry_count: int = 0
    max_retries: int = 0
    duration: float | None = None
    error_type: ProviderErrorType | None = None
    additional_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for logging"""
        context = {
            "provider": self.provider_slug,
            "endpoint": self.endpoint_url,
            "status_code": self.status_code,
            "models_fetched": self.models_fetched,
        }

        if self.models_expected is not None:
            context["models_expected"] = self.models_expected
            context["models_delta"] = self.models_fetched - self.models_expected

        if self.retry_count > 0:
            context["retry"] = f"{self.retry_count}/{self.max_retries}"

        if self.duration is not None:
            context["duration"] = f"{self.duration:.2f}s"
            if self.models_fetched > 0:
                context["rate"] = f"{self.models_fetched / self.duration:.0f} models/sec"

        if self.error_type:
            context["error_type"] = self.error_type.value

        # Add any additional context
        context.update(self.additional_context)

        return context


def classify_provider_error(error: Exception) -> ProviderErrorType:
    """
    Classify an exception into a ProviderErrorType.

    Args:
        error: The exception to classify

    Returns:
        ProviderErrorType enum value
    """
    import httpx

    error_name = type(error).__name__

    # Timeout errors
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return ProviderErrorType.API_TIMEOUT

    # HTTP errors
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code == 401 or status_code == 403:
            return ProviderErrorType.AUTH_FAILURE
        elif status_code == 429:
            return ProviderErrorType.RATE_LIMITED
        else:
            return ProviderErrorType.HTTP_ERROR

    # Network errors
    if isinstance(error, (httpx.ConnectError, httpx.NetworkError, ConnectionError)):
        return ProviderErrorType.NETWORK_ERROR

    # Parsing errors
    if "json" in error_name.lower() or isinstance(error, (ValueError, TypeError)):
        return ProviderErrorType.PARSING_ERROR

    # Configuration errors
    if isinstance(error, (KeyError, AttributeError)) or "config" in str(error).lower():
        return ProviderErrorType.CONFIGURATION_ERROR

    return ProviderErrorType.UNKNOWN


def log_provider_fetch_error(
    provider_slug: str,
    error: Exception,
    context: ProviderFetchContext | None = None,
) -> None:
    """
    Log a provider fetch error with standardized formatting and context.

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'deepinfra')
        error: The exception that occurred
        context: Optional context information about the fetch operation
    """
    if context is None:
        context = ProviderFetchContext(provider_slug=provider_slug)

    # Classify error if not already set
    if context.error_type is None:
        context.error_type = classify_provider_error(error)

    # Build error message
    error_msg = sanitize_for_logging(str(error))
    error_type_name = type(error).__name__

    # Get context dict for logging
    context_dict = context.to_dict()

    # Format context as readable string
    context_parts = []
    for key, value in context_dict.items():
        if key == "provider":
            continue  # Already in message prefix
        if value is not None:
            context_parts.append(f"{key}={value}")

    context_str = " | ".join(context_parts) if context_parts else "no additional context"

    # Log the error
    logger.error(
        f"[{provider_slug.upper()}] FETCH FAILED | "
        f"Error: {error_type_name} | "
        f"{context_str} | "
        f"Details: {error_msg}"
    )


def log_provider_fetch_success(
    provider_slug: str,
    models_count: int,
    duration: float | None = None,
    additional_context: dict[str, Any] | None = None,
) -> None:
    """
    Log a successful provider fetch with standardized formatting.

    Args:
        provider_slug: Provider slug
        models_count: Number of models fetched
        duration: Time taken in seconds
        additional_context: Additional context to include in log
    """
    parts = [f"Models: {models_count}"]

    if duration is not None:
        parts.append(f"Duration: {duration:.2f}s")
        if models_count > 0:
            parts.append(f"Rate: {models_count / duration:.0f} models/sec")

    if additional_context:
        for key, value in additional_context.items():
            parts.append(f"{key}={value}")

    context_str = " | ".join(parts)

    logger.info(f"[{provider_slug.upper()}] FETCH SUCCESS | {context_str}")


def log_provider_fetch_warning(
    provider_slug: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    """
    Log a provider fetch warning with standardized formatting.

    Args:
        provider_slug: Provider slug
        message: Warning message
        context: Optional context information
    """
    if context:
        context_parts = [f"{k}={v}" for k, v in context.items() if v is not None]
        context_str = " | ".join(context_parts) if context_parts else ""
        logger.warning(f"[{provider_slug.upper()}] WARNING | {message} | {context_str}")
    else:
        logger.warning(f"[{provider_slug.upper()}] WARNING | {message}")


def log_provider_cache_operation(
    provider_slug: str,
    operation: str,
    success: bool,
    models_count: int | None = None,
    error: Exception | None = None,
) -> None:
    """
    Log a cache operation for a provider.

    Args:
        provider_slug: Provider slug
        operation: Cache operation (e.g., 'SET', 'GET', 'INVALIDATE')
        success: Whether the operation succeeded
        models_count: Number of models involved (if applicable)
        error: Exception if operation failed
    """
    status = "SUCCESS" if success else "FAILED"
    parts = [f"Operation: {operation}", f"Status: {status}"]

    if models_count is not None:
        parts.append(f"Models: {models_count}")

    if error:
        parts.append(f"Error: {type(error).__name__}: {sanitize_for_logging(str(error))}")

    context_str = " | ".join(parts)

    if success:
        logger.debug(f"[{provider_slug.upper()}] CACHE | {context_str}")
    else:
        logger.warning(f"[{provider_slug.upper()}] CACHE | {context_str}")


def format_duration(seconds: float) -> str:
    """
    Format duration in a human-readable way.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., '1.23s', '45.6s', '2m 30s')
    """
    if seconds < 60:
        return f"{seconds:.2f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def format_model_count(count: int, singular: str = "model", plural: str = "models") -> str:
    """
    Format model count with proper pluralization.

    Args:
        count: Number of models
        singular: Singular form of the word
        plural: Plural form of the word

    Returns:
        Formatted string (e.g., '1 model', '42 models')
    """
    word = singular if count == 1 else plural
    return f"{count} {word}"
