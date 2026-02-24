"""
Passive health monitoring service.

This service captures health metrics from actual user API calls
without requiring proactive health checks or knowing user identity.
"""

import asyncio
import logging
from typing import Any

from src.db.model_health import record_model_call

logger = logging.getLogger(__name__)


async def capture_model_health(
    provider: str,
    model: str,
    response_time_ms: float,
    status: str = "success",
    error_message: str | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    """
    Capture health metrics from a model call.

    This function runs as a background task and does not block the API response.
    It extracts token usage from the response and records it in the health tracking table.

    Args:
        provider: The AI provider name (e.g., 'openrouter', 'portkey')
        model: The model identifier
        response_time_ms: Response time in milliseconds
        status: Call status ('success', 'error', 'timeout', 'rate_limited', etc.)
        error_message: Optional error message if status is 'error'
        usage: Optional usage dictionary containing token counts
               Expected format: {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
               or {"input_tokens": X, "output_tokens": Y}
    """
    try:
        # Extract token information from usage dict
        input_tokens = None
        output_tokens = None
        total_tokens = None

        if usage:
            # Handle different usage dict formats
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
            output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            total_tokens = usage.get("total_tokens")

            # Calculate total if not provided
            if total_tokens is None and input_tokens is not None and output_tokens is not None:
                total_tokens = input_tokens + output_tokens

        # Record the model call in background (non-blocking)
        await asyncio.to_thread(
            record_model_call,
            provider=provider,
            model=model,
            response_time_ms=response_time_ms,
            status=status,
            error_message=error_message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

        logger.debug(
            f"Recorded health metric for {provider}/{model}: "
            f"status={status}, latency={response_time_ms}ms, tokens={total_tokens}"
        )

    except Exception as e:
        # Log errors but don't let them affect the API response
        logger.error(f"Failed to capture health metric for {provider}/{model}: {e}")


def extract_provider_from_request(request_model: str, used_provider: str | None = None) -> str:
    """
    Extract the provider name from a model request.

    Args:
        request_model: The model string from the request (may include provider prefix)
        used_provider: The provider that was actually used (if known)

    Returns:
        Provider name (e.g., 'openrouter', 'portkey', 'huggingface')
    """
    if used_provider:
        return used_provider

    # Try to extract provider from model string
    # Common patterns: "provider/model" or "provider:model"
    for separator in ["/", ":"]:
        if separator in request_model:
            parts = request_model.split(separator, 1)
            return parts[0]

    # Default to 'onerouter' if no provider prefix found
    return "onerouter"


def normalize_model_name(model: str) -> str:
    """
    Normalize a model name by removing provider prefixes.

    Args:
        model: The model string (may include provider prefix)

    Returns:
        Normalized model name without provider prefix
    """
    # Remove provider prefix if present
    for separator in ["/", ":"]:
        if separator in model:
            parts = model.split(separator, 1)
            return parts[1] if len(parts) > 1 else model

    return model
