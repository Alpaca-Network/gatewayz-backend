import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from httpx import ConnectError, ReadTimeout, RemoteProtocolError

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _execute_with_connection_retry(
    operation: Callable[[], T],
    operation_name: str,
    max_retries: int = 3,
    initial_delay: float = 0.1,
) -> T:
    """
    Execute a Supabase operation with retry logic for transient connection errors.

    Handles HTTP/2 connection resets (GOAWAY frames), server disconnects,
    and other transient network issues that can occur when reusing connections
    in high-concurrency scenarios.

    Args:
        operation: The operation to execute
        operation_name: Name of the operation for logging
        max_retries: Maximum number of retry attempts (default: 3)
        initial_delay: Initial delay in seconds before first retry (default: 0.1)

    Returns:
        The result of the operation

    Raises:
        The last exception encountered if all retries fail
    """
    last_exception = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return operation()
        except (RemoteProtocolError, ConnectError, ReadTimeout) as e:
            last_exception = e
            error_type = type(e).__name__

            if attempt < max_retries:
                logger.warning(
                    f"{operation_name} failed with {error_type}: {e}. "
                    f"Retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                logger.error(
                    f"{operation_name} failed after {max_retries} retries with {error_type}: {e}"
                )
        except Exception as e:
            # For non-transient errors, fail immediately
            logger.error(f"{operation_name} failed with non-retryable error: {e}")
            raise

    # If we get here, all retries failed
    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed without exception details")


def get_all_latest_models(
    limit: int | None = None, offset: int | None = None
) -> list[dict[str, Any]]:
    """Get all data from latest_models table for ranking page with logo URLs.

    This function uses retry logic to handle transient HTTP/2 connection errors
    (GOAWAY frames, connection resets) that can occur with the global Supabase client.
    """
    try:
        client = get_supabase_client()

        def execute_query():
            # Build query with optional pagination
            query = client.table("latest_models").select("*")

            # Apply ordering by rank (ascending order - rank 1 first)
            query = query.order("rank", desc=False)

            # Apply pagination if specified
            if offset:
                query = query.range(offset, offset + (limit or 50) - 1)
            elif limit:
                query = query.limit(limit)

            return query.execute()

        # Execute with retry logic for transient connection errors
        result = _execute_with_connection_retry(
            operation=execute_query,
            operation_name="get_all_latest_models",
        )

        if not result.data:
            logger.info("No models found in latest_models table")
            return []

        # Enhance models with logo URLs if not present
        enhanced_models = []
        for model in result.data:
            enhanced_model = model.copy()

            # Generate logo URL if not present
            if "logo_url" not in model or not model.get("logo_url"):
                logo_url = generate_logo_url_from_author(model.get("author", ""))
                if logo_url:
                    enhanced_model["logo_url"] = logo_url

            enhanced_models.append(enhanced_model)

        logger.info(
            f"Retrieved {len(enhanced_models)} models from latest_models table with logo URLs"
        )
        return enhanced_models

    except (RemoteProtocolError, ConnectError, ReadTimeout) as e:
        # Connection errors should be logged but re-raised as RuntimeError
        # for consistent error handling upstream
        logger.error(f"Failed to get latest models after retries: {e}")
        raise RuntimeError(f"Failed to get latest models: {e}") from e
    except Exception as e:
        logger.error(f"Failed to get latest models: {e}")
        raise RuntimeError(f"Failed to get latest models: {e}") from e


def generate_logo_url_from_author(author: str) -> str:
    """Generate logo URL from author name using Google favicon service"""
    if not author:
        return None

    # Map author names to domains
    author_domain_map = {
        "openai": "openai.com",
        "anthropic": "anthropic.com",
        "google": "google.com",
        "x-ai": "x.ai",
        "deepseek": "deepseek.com",
        "z-ai": "zhipuai.cn",
        "meta": "meta.com",
        "microsoft": "microsoft.com",
        "cohere": "cohere.com",
        "mistralai": "mistral.ai",
        "perplexity": "perplexity.ai",
        "amazon": "aws.amazon.com",
        "baidu": "baidu.com",
        "tencent": "tencent.com",
        "alibaba": "alibaba.com",
        "ai21": "ai21.com",
        "inflection": "inflection.ai",
    }

    # Get domain for author
    domain = author_domain_map.get(author.lower())
    if not domain:
        # Try to use author as domain if it looks like a domain
        if "." in author:
            domain = author
        else:
            return None

    # Generate Google favicon URL
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


def get_all_latest_apps() -> list[dict[str, Any]]:
    """Get all data from latest_apps table for ranking page.

    This function uses retry logic to handle transient HTTP/2 connection errors
    (GOAWAY frames, connection resets) that can occur with the global Supabase client.
    """
    try:
        client = get_supabase_client()

        def execute_query():
            return client.table("latest_apps").select("*").execute()

        # Execute with retry logic for transient connection errors
        result = _execute_with_connection_retry(
            operation=execute_query,
            operation_name="get_all_latest_apps",
        )

        if not result.data:
            logger.info("No apps found in latest_apps table")
            return []

        logger.info(f"Retrieved {len(result.data)} apps from latest_apps table")
        return result.data

    except (RemoteProtocolError, ConnectError, ReadTimeout) as e:
        # Connection errors should be logged but re-raised as RuntimeError
        # for consistent error handling upstream
        logger.error(f"Failed to get latest apps after retries: {e}")
        raise RuntimeError(f"Failed to get latest apps: {e}") from e
    except Exception as e:
        logger.error(f"Failed to get latest apps: {e}")
        raise RuntimeError(f"Failed to get latest apps: {e}") from e
