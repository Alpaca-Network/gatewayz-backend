"""
Helper Functions for Stream Processing

This module contains extracted helper functions from stream_generator()
to improve code organization and reduce function complexity.
"""

import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def estimate_tokens_from_content(
    accumulated_content: str,
    messages: list,
) -> Tuple[int, int, int]:
    """
    Estimate token counts when provider doesn't provide usage data.

    Uses rough heuristic: 1 token ≈ 4 characters

    Args:
        accumulated_content: The complete response content
        messages: The request messages (for prompt token estimation)

    Returns:
        Tuple of (prompt_tokens, completion_tokens, total_tokens)
    """
    # Estimate completion tokens from response
    completion_tokens = max(1, len(accumulated_content) // 4)

    # Calculate prompt tokens from messages
    prompt_chars = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            prompt_chars += len(content)
        elif isinstance(content, list):
            # For multimodal content, extract text parts
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    prompt_chars += len(item.get("text", ""))

    prompt_tokens = max(1, prompt_chars // 4)
    total_tokens = prompt_tokens + completion_tokens

    return prompt_tokens, completion_tokens, total_tokens


def calculate_stream_timing_data(
    ttfc_seconds: Optional[float],
    elapsed: float,
    completion_tokens: int,
    prompt_tokens: int,
    total_tokens: int,
    request_received_at: Optional[float],
) -> Dict:
    """
    Calculate timing metrics for streaming response.

    Args:
        ttfc_seconds: Time to first chunk in seconds
        elapsed: Total elapsed time in seconds
        completion_tokens: Number of completion tokens
        prompt_tokens: Number of prompt tokens
        total_tokens: Total token count
        request_received_at: Server timestamp when request was received

    Returns:
        Dictionary containing timing data for client
    """
    import time

    # Calculate streaming-specific metrics
    streaming_duration = None
    tokens_per_second = None

    if ttfc_seconds is not None:
        streaming_duration = elapsed - ttfc_seconds
        if completion_tokens > 0 and streaming_duration > 0:
            tokens_per_second = completion_tokens / streaming_duration

    # Calculate server timestamps for client latency measurement
    server_responded_at = time.time()

    # Build timing data structure
    timing_data = {
        "type": "timing",
        "timing": {
            "ttfc_ms": round(ttfc_seconds * 1000, 1) if ttfc_seconds else None,
            "total_ms": round(elapsed * 1000, 1),
            "streaming_ms": round(streaming_duration * 1000, 1) if streaming_duration else None,
            "tokens_per_second": round(tokens_per_second, 1) if tokens_per_second else None,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "server_received_at": request_received_at,
            "server_responded_at": server_responded_at,
        }
    }

    return timing_data


def map_streaming_error_message(error: Exception) -> Tuple[str, str]:
    """
    Map exception to user-friendly error message and type.

    Args:
        error: The exception that occurred

    Returns:
        Tuple of (error_message, error_type) for client display
    """
    error_str = str(error).lower()
    error_message = "Streaming error occurred"
    error_type = "stream_error"

    # Check for rate limit errors
    if "rate limit" in error_str or "429" in error_str or "too many" in error_str:
        error_message = "Rate limit exceeded. Please wait a moment and try again."
        error_type = "rate_limit_error"
    # Check for authentication errors
    elif "401" in error_str or "unauthorized" in error_str or "authentication" in error_str:
        error_message = "Authentication failed. Please check your API key or sign in again."
        error_type = "auth_error"
    # Check for provider/upstream errors
    elif "upstream" in error_str or "provider" in error_str or "503" in error_str or "502" in error_str:
        error_message = f"Provider temporarily unavailable: {str(error)[:200]}"
        error_type = "provider_error"
    # Check for timeout errors
    elif "timeout" in error_str or "timed out" in error_str:
        error_message = "Request timed out. The model may be overloaded. Please try again."
        error_type = "timeout_error"
    # Check for model not found errors
    elif "not found" in error_str or "404" in error_str:
        error_message = f"Model or resource not found: {str(error)[:200]}"
        error_type = "not_found_error"
    # For other errors, include a sanitized version of the error message
    else:
        # Include the actual error message but truncate it for safety
        sanitized_msg = str(error)[:300].replace('\n', ' ').replace('\r', ' ')
        error_message = f"Streaming error: {sanitized_msg}"

    return error_message, error_type
