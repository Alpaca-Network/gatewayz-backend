"""
API Key Lookup Utilities
Provides robust API key lookup with retry logic and error handling.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def get_api_key_id_with_retry(
    api_key: str,
    max_retries: int = 3,
    retry_delay: float = 0.1,
) -> int | None:
    """
    Get API key ID with retry logic for transient failures.

    This function attempts to look up the API key ID from the database,
    with automatic retries for transient failures like connection errors.

    Args:
        api_key: The API key string to look up
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Delay between retries in seconds (default: 0.1)

    Returns:
        API key ID if found, None otherwise
    """
    if not api_key:
        return None

    # Avoid retrying for known invalid keys
    if api_key in {"local-dev-bypass-key", "anonymous"}:
        logger.debug(f"Skipping lookup for special key: {api_key}")
        return None

    from src.db import api_keys as api_keys_module

    # Import metrics for tracking
    try:
        from src.services.prometheus_metrics import (
            api_key_lookup_attempts,
            api_key_tracking_failures,
        )

        metrics_available = True
    except ImportError:
        metrics_available = False

    last_error = None

    for attempt in range(max_retries):
        try:
            api_key_record = await asyncio.to_thread(api_keys_module.get_api_key_by_key, api_key)

            if api_key_record:
                api_key_id = api_key_record.get("id")
                if api_key_id:
                    if attempt > 0:
                        logger.info(
                            f"Successfully retrieved API key ID after {attempt + 1} attempts"
                        )
                        if metrics_available:
                            api_key_lookup_attempts.labels(status="retry").inc()
                    if metrics_available:
                        api_key_lookup_attempts.labels(status="success").inc()
                    return api_key_id
                else:
                    logger.warning(
                        f"API key record found but missing 'id' field: {api_key_record}"
                    )
                    if metrics_available:
                        api_key_tracking_failures.labels(reason="invalid_record").inc()
                    return None
            else:
                # Key not found in database - don't retry
                logger.debug(f"API key not found in database (attempt {attempt + 1})")
                if metrics_available:
                    api_key_tracking_failures.labels(reason="not_found").inc()
                return None

        except Exception as e:
            last_error = e
            if metrics_available:
                api_key_lookup_attempts.labels(status="failed").inc()

            if attempt < max_retries - 1:
                logger.warning(
                    f"API key lookup failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying..."
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"API key lookup failed after {max_retries} attempts: {e}",
                    exc_info=True,
                )

    # All retries exhausted
    logger.error(f"Failed to retrieve API key ID after {max_retries} attempts. Last error: {last_error}")
    if metrics_available:
        api_key_tracking_failures.labels(reason="lookup_failed").inc()
    return None


def mask_api_key_for_logging(api_key: str | None) -> str:
    """
    Mask API key for safe logging.

    Args:
        api_key: API key to mask

    Returns:
        Masked API key string
    """
    if not api_key:
        return "None"

    if len(api_key) <= 8:
        return "***"

    return f"{api_key[:4]}...{api_key[-4:]}"
