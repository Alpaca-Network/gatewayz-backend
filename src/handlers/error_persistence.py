"""Error persistence for failed chat completion requests."""

import logging
import time

logger = logging.getLogger(__name__)


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
