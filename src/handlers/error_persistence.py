"""Error persistence for failed chat completion requests."""

import logging
import time

from src.utils.errors import sanitize_provider_error_for_user
from src.utils.provider_error_logging import ProviderErrorType, classify_provider_error

logger = logging.getLogger(__name__)

# Error categories whose message text originates from the upstream provider or
# transport (HTTP status text, timeout/network exceptions) rather than from
# arbitrary internal application state. sanitize_provider_error_for_user only
# strips URLs and hex-secret-shaped tokens — it does NOT strip arbitrary text —
# so only these categories get a sanitized rendering of str(error) persisted.
_PROVIDER_MESSAGE_TYPES = frozenset(
    {
        ProviderErrorType.API_TIMEOUT,
        ProviderErrorType.HTTP_ERROR,
        ProviderErrorType.AUTH_FAILURE,
        ProviderErrorType.RATE_LIMITED,
        ProviderErrorType.NETWORK_ERROR,
    }
)


def format_error_for_persistence(error: Exception) -> str:
    """Build a safe, bounded string for chat_completion_requests.error_message.

    Guarantee (threat model L6/G5 — error_message must never echo user input):
    for exceptions classified as provider/network-shaped, this returns the
    exception type name plus a URL- and hex-secret-scrubbed, 200-char-truncated
    rendering of str(error). For every other exception type — parsing,
    configuration, database, cache, unknown — whose message routinely embeds
    arbitrary internal state (a dict key, an attribute name, part of a request
    payload), str(error) is never included; only the type name and a generic
    category are persisted.
    """
    error_type = type(error).__name__
    category = classify_provider_error(error)
    if category in _PROVIDER_MESSAGE_TYPES:
        detail = sanitize_provider_error_for_user(str(error))[:200]
        return f"{error_type}: {detail}"
    return f"{error_type}: {category.value}"


async def save_failed_request(
    _to_thread,
    save_chat_completion_request_with_cost,
    request_id: str,
    model: str,
    original_model: str,
    prompt_tokens: int,
    start_time: float,
    error: Exception,
    error_message: str,
    user: dict | None,
    provider: str | None,
    api_key_id: int | None,
    is_anonymous: bool,
) -> None:
    """Save a failed chat completion request to the database.

    Called from except blocks in chat_completions and unified_responses.
    """
    if not request_id:
        return

    try:
        # Calculate elapsed time
        error_elapsed = time.monotonic() - start_time if start_time else 0

        # Save failed request to database with cost tracking (costs are 0 for failed requests)
        await _to_thread(
            save_chat_completion_request_with_cost,
            request_id=request_id,
            model_name=model if model else (original_model if original_model else "unknown"),
            input_tokens=prompt_tokens if prompt_tokens else 0,
            output_tokens=0,  # No output on error
            processing_time_ms=int(error_elapsed * 1000),
            cost_usd=0.0,
            input_cost_usd=0.0,
            output_cost_usd=0.0,
            pricing_source="error",
            status="failed",
            error_message=error_message,
            user_id=user["id"] if user else None,
            provider_name=provider,
            model_id=None,
            api_key_id=api_key_id,
            is_anonymous=is_anonymous,
        )
    except Exception as save_err:
        logger.debug(f"Failed to save failed request metadata: {save_err}")
