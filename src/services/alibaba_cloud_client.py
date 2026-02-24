import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import TypeVar

from openai import OpenAI

try:  # pragma: no cover - defensive import for differing OpenAI SDKs
    from openai import AuthenticationError
except ImportError:  # pragma: no cover
    AuthenticationError = Exception  # type: ignore[assignment]

from src.config import Config
from src.config.redis_config import get_redis_manager
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.model_catalog_cache import cache_gateway_catalog, get_cached_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"

_REGION_ENDPOINTS = {
    "china": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "international": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}
_REGION_DESCRIPTIONS = {
    "china": "dashscope.aliyuncs.com (China/Beijing)",
    "international": "dashscope-intl.aliyuncs.com (International/Singapore)",
}
_VALID_REGIONS = tuple(_REGION_ENDPOINTS.keys())
_explicit_region = os.environ.get("ALIBABA_CLOUD_REGION")
_inferred_region: str | None = None
_region_lock = Lock()
T = TypeVar("T")


def _region_specific_api_key(region: str | None) -> str | None:
    normalized = _normalize_region(region)
    if normalized == "china":
        return getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None)
    if normalized == "international":
        return getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None)
    return None


def _get_region_api_key(region: str | None) -> str | None:
    """Get the API key for a specific region.

    Priority order:
    1. Region-specific key (e.g., ALIBABA_CLOUD_API_KEY_CHINA)
    2. Generic key (ALIBABA_CLOUD_API_KEY) as fallback
    3. Any other region's key as last resort (enables failover when user
       misconfigured which key goes with which region)

    Returns the key for the region, or None if no key is configured.
    """
    region_specific = _region_specific_api_key(region)
    if region_specific:
        return region_specific

    generic_key = getattr(Config, "ALIBABA_CLOUD_API_KEY", None)
    if generic_key:
        return generic_key

    # Last resort: use any available key to enable failover
    # This handles the case where user set the wrong region-specific key
    for other_region in _VALID_REGIONS:
        other_key = _region_specific_api_key(other_region)
        if other_key:
            return other_key

    return None


def _any_api_key_configured() -> bool:
    return any(
        (
            getattr(Config, "ALIBABA_CLOUD_API_KEY", None),
            getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None),
            getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None),
        )
    )


def _normalize_region(region: str | None) -> str:
    if not region:
        return "international"
    normalized = region.lower()
    if normalized not in _VALID_REGIONS:
        return "international"
    return normalized


def _region_attempt_order() -> list[str]:
    explicit = _normalize_region(_explicit_region) if _explicit_region else None
    if explicit:
        return [explicit] if _get_region_api_key(explicit) else []

    attempts: list[str] = []
    if _inferred_region:
        attempts.append(_inferred_region)

    default_region = _normalize_region(getattr(Config, "ALIBABA_CLOUD_REGION", None))
    if default_region not in attempts:
        attempts.append(default_region)

    for region in _VALID_REGIONS:
        if region not in attempts:
            attempts.append(region)
    return [region for region in attempts if _get_region_api_key(region)]


def _remember_successful_region(region: str) -> None:
    if _explicit_region:
        return
    global _inferred_region
    with _region_lock:
        _inferred_region = region


def _describe_region(region: str) -> str:
    return _REGION_DESCRIPTIONS.get(region, region)


def _is_auth_error(error: Exception) -> bool:
    if isinstance(error, AuthenticationError):
        return True

    message = str(error).lower()
    indicators = [
        "incorrect api key",
        "invalid_api_key",
        "invalid api key",
        "error code: 401",
        "status code: 401",
        "unauthorized",
    ]
    return any(indicator in message for indicator in indicators)


def _is_quota_error(error: Exception) -> bool:
    """Check if an error is a quota/rate limit error (429).

    Quota errors indicate the account has exceeded its quota and should
    not be retried immediately. These are different from transient rate
    limits and require user action (billing/plan upgrade).
    """
    message = str(error).lower()
    indicators = [
        "insufficient_quota",
        "exceeded your current quota",
        "error code: 429",
        "status code: 429",
        "quota exceeded",
        "rate_limit_exceeded",
    ]
    return any(indicator in message for indicator in indicators)


class QuotaExceededError(Exception):
    """Raised when Alibaba Cloud returns a quota exceeded error (429).

    This error indicates the account has exceeded its quota and requires
    user action (billing/plan upgrade). It should be cached to prevent
    repeated API calls that will fail.
    """

    pass


def _execute_with_region_failover(  # noqa: UP047
    operation_name: str, fn: Callable[[OpenAI], T]
) -> T:
    attempts = _region_attempt_order()
    if not attempts:
        raise ValueError(
            "Alibaba Cloud API key not configured for any region. "
            "Set ALIBABA_CLOUD_API_KEY or a region-specific key."
        )

    if len(attempts) > 1:
        logger.debug(
            "Alibaba Cloud %s will attempt %d regions in order: %s",
            operation_name,
            len(attempts),
            ", ".join(_describe_region(r) for r in attempts),
        )

    last_error: Exception | None = None

    for idx, region in enumerate(attempts):
        try:
            client = get_alibaba_cloud_client(region_override=region)
            result = fn(client)
            _remember_successful_region(region)
            if idx > 0:
                logger.info(
                    "Alibaba Cloud %s succeeded after switching to %s",
                    operation_name,
                    _describe_region(region),
                )
            return result
        except Exception as exc:  # noqa: PERF203 - clarity over premature micro-opt
            last_error = exc
            is_auth = _is_auth_error(exc)
            is_quota = _is_quota_error(exc)
            has_more_regions = idx < len(attempts) - 1
            should_retry = is_auth and has_more_regions

            # Handle quota errors specially - don't retry, wrap in QuotaExceededError
            if is_quota:
                logger.warning(
                    "Alibaba Cloud %s quota exceeded for %s. "
                    "Please check your plan and billing details at "
                    "https://www.alibabacloud.com/help/en/model-studio/error-code#token-limit",
                    operation_name,
                    _describe_region(region),
                )
                raise QuotaExceededError(str(exc)) from exc

            if should_retry:
                next_region = attempts[idx + 1]
                logger.warning(
                    "Alibaba Cloud %s rejected credentials at %s (error: %s). " "Retrying with %s.",
                    operation_name,
                    _describe_region(region),
                    exc,
                    _describe_region(next_region),
                )
                continue

            # Log why we're not retrying for debugging
            # Note: should_retry is False here, meaning either:
            # - is_auth=True but has_more_regions=False (exhausted all regions)
            # - is_auth=False (non-auth error, don't retry)
            if is_auth:
                logger.error(
                    "Alibaba Cloud %s failed for %s (error: %s). "
                    "No more regions to try (attempted %d of %d regions).",
                    operation_name,
                    _describe_region(region),
                    exc,
                    idx + 1,
                    len(attempts),
                )
            else:
                logger.error(
                    "Alibaba Cloud %s failed for %s (error: %s). "
                    "Error is not an auth error, not retrying with other regions.",
                    operation_name,
                    _describe_region(region),
                    exc,
                )
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Alibaba Cloud operation failed without error details")


def get_alibaba_cloud_client(region_override: str | None = None):
    """Get Alibaba Cloud client with proper configuration

    Supports two regions:
    - International (Singapore): dashscope-intl.aliyuncs.com (default)
    - China (Beijing): dashscope.aliyuncs.com

    Region selection will automatically fall back if credentials fail for the default endpoint.
    """
    try:
        if not _any_api_key_configured():
            raise ValueError(
                "Alibaba Cloud API key not configured. "
                "Set ALIBABA_CLOUD_API_KEY or region-specific keys "
                "(ALIBABA_CLOUD_API_KEY_INTERNATIONAL / ALIBABA_CLOUD_API_KEY_CHINA)."
            )

        region = _normalize_region(region_override or getattr(Config, "ALIBABA_CLOUD_REGION", None))
        api_key = _get_region_api_key(region)
        if not api_key:
            raise ValueError(
                f"No Alibaba Cloud API key configured for {_describe_region(region)}. "
                "Provide ALIBABA_CLOUD_API_KEY or the matching region-specific key."
            )
        base_url = _REGION_ENDPOINTS[region]

        logger.debug("Using Alibaba Cloud endpoint %s", _describe_region(region))

        return OpenAI(
            base_url=base_url,
            api_key=api_key,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Alibaba Cloud client: {e}")
        raise


def list_alibaba_models():
    """List models from Alibaba Cloud with automatic region fallback."""
    return _execute_with_region_failover("models.list", lambda client: client.models.list())


def make_alibaba_cloud_request_openai(messages, model, **kwargs):
    """Make request to Alibaba Cloud using OpenAI-compatible API"""
    return _execute_with_region_failover(
        "chat.completions.create",
        lambda client: client.chat.completions.create(model=model, messages=messages, **kwargs),
    )


def process_alibaba_cloud_response(response):
    """Process Alibaba Cloud response to extract relevant data"""
    try:
        # Validate response has expected structure
        if not hasattr(response, "choices") or not response.choices:
            logger.error("Response missing 'choices' attribute")
            raise ValueError("Invalid Alibaba Cloud response format: missing choices")

        choices = []
        for choice in response.choices:
            # Extract message with fallback for different response formats
            if hasattr(choice, "message"):
                msg = extract_message_with_tools(choice.message)
            else:
                logger.warning("Choice missing 'message' attribute, creating default")
                msg = {"role": "assistant", "content": ""}

            finish_reason = getattr(choice, "finish_reason", "stop")
            choice_index = getattr(choice, "index", 0)

            choices.append(
                {
                    "index": choice_index,
                    "message": msg,
                    "finish_reason": finish_reason,
                }
            )

        # Build response with safe attribute access
        result = {
            "id": getattr(response, "id", "unknown"),
            "object": getattr(response, "object", "chat.completion"),
            "created": getattr(response, "created", 0),
            "model": getattr(response, "model", "unknown"),
            "choices": choices,
        }

        # Handle usage data safely
        if hasattr(response, "usage") and response.usage:
            result["usage"] = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }
        else:
            result["usage"] = {}

        logger.debug(f"Processed Alibaba Cloud response: {result}")
        return result
    except Exception as e:
        logger.error(f"Failed to process Alibaba Cloud response: {e}")
        raise


def make_alibaba_cloud_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Alibaba Cloud using OpenAI-compatible API"""
    return _execute_with_region_failover(
        "chat.completions.create (stream)",
        lambda client: client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            **kwargs,
        ),
    )


def validate_stream_chunk(chunk):
    """Validate and ensure Alibaba Cloud stream chunk has required attributes"""
    try:
        # DashScope returns OpenAI-compatible format, but ensure attributes exist
        if not hasattr(chunk, "choices") or not chunk.choices:
            logger.warning("Stream chunk missing 'choices' attribute")
            return False

        for choice in chunk.choices:
            if not hasattr(choice, "delta"):
                logger.warning("Choice missing 'delta' attribute")
                return False

        return True
    except Exception as e:
        logger.error(f"Error validating stream chunk: {e}")
        return False


# ============================================================================
# Model Catalog Functions
# ============================================================================


def _is_alibaba_quota_error_cached() -> bool:
    """Check if we're in a quota error backoff period.

    Returns True if a quota error was recently recorded and we should skip
    making API calls to avoid log spam.
    """
    try:
        redis_manager = get_redis_manager()
        quota_error = redis_manager.get_json("alibaba:quota_error")
        if not quota_error:
            return False

        timestamp_str = redis_manager.get("alibaba:quota_error_timestamp")
        if not timestamp_str:
            return False

        timestamp = datetime.fromisoformat(timestamp_str)
        backoff = int(redis_manager.get("alibaba:quota_error_backoff") or "900")  # Default 15 min
        age = (datetime.now(UTC) - timestamp).total_seconds()
        return age < backoff
    except Exception as e:
        logger.debug(f"Failed to check alibaba quota error cache: {e}")
        return False


def _set_alibaba_quota_error():
    """Record a quota error with backoff timing.

    Note: We intentionally do NOT set the main cache timestamp here.
    This ensures that the cache appears "stale" so that get_cached_models()
    will call fetch_models_from_alibaba(), where the quota error backoff
    check (_is_alibaba_quota_error_cached) is evaluated. If we set timestamp,
    the 1-hour cache TTL would override our 15-minute quota error backoff.
    """
    try:
        redis_manager = get_redis_manager()
        redis_manager.set_json("alibaba:quota_error", True, ttl=900)  # 15 min TTL
        redis_manager.set("alibaba:quota_error_timestamp", datetime.now(UTC).isoformat(), ttl=900)
        redis_manager.set("alibaba:quota_error_backoff", "900", ttl=900)  # 15 min backoff
        # Clear the model cache
        cache_gateway_catalog("alibaba", [])
    except Exception as e:
        logger.warning(f"Failed to set alibaba quota error state: {e}")


def _clear_alibaba_quota_error():
    """Clear quota error state after successful fetch."""
    try:
        redis_manager = get_redis_manager()
        redis_manager.delete("alibaba:quota_error")
        redis_manager.delete("alibaba:quota_error_timestamp")
        redis_manager.delete("alibaba:quota_error_backoff")
    except Exception as e:
        logger.debug(f"Failed to clear alibaba quota error state: {e}")


def normalize_alibaba_model(model) -> dict | None:
    """Normalize Alibaba Cloud model to catalog schema

    Alibaba models use OpenAI-compatible naming conventions.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Alibaba Cloud model missing 'id': %s", sanitize_for_logging(str(model)))
        return None

    raw_model_name = getattr(model, "name", model_id)
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    model_name = clean_model_name(raw_model_name)

    try:
        normalized = {
            "id": model_id,
            "slug": f"alibaba/{model_id}",
            "canonical_slug": f"alibaba/{model_id}",
            "hugging_face_id": None,
            "name": model_name,
            "created": getattr(model, "created_at", None),
            "description": getattr(model, "description", "Model from Alibaba Cloud"),
            "context_length": getattr(model, "context_length", 4096),
            "architecture": {
                "modality": MODALITY_TEXT_TO_TEXT,
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "instruct_type": "chat",
            },
            "pricing": {
                "prompt": "0",
                "completion": "0",
                "request": "0",
                "image": "0",
            },
            "per_request_limits": None,
            "supported_parameters": [],
            "default_parameters": {},
            "provider_slug": "alibaba",
            "provider_site_url": "https://dashscope.aliyun.com",
            "model_logo_url": None,
            "source_gateway": "alibaba",
        }
        return enrich_model_with_pricing(normalized, "alibaba-cloud")
    except Exception as e:
        logger.error("Failed to normalize Alibaba Cloud model: %s", sanitize_for_logging(str(e)))
        return None


def fetch_models_from_alibaba():
    """Fetch models from Alibaba Cloud (DashScope) via OpenAI-compatible API

    Alibaba Cloud provides access to Qwen models through a unified OpenAI-compatible endpoint.
    """
    try:
        # Check if API key is configured
        if not (
            Config.ALIBABA_CLOUD_API_KEY
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None)
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None)
        ):
            logger.debug("Alibaba Cloud API key not configured - skipping model fetch")
            # Cache empty result to avoid repeated warnings
            cache_gateway_catalog("alibaba", [])
            return []

        # Check if we're in quota error backoff period
        if _is_alibaba_quota_error_cached():
            logger.debug(
                "Alibaba Cloud quota error in backoff period - skipping API call. "
                "Will retry after backoff expires."
            )
            return get_cached_gateway_catalog("alibaba") or []

        response = list_alibaba_models()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Alibaba Cloud")
            return []

        # Normalize models (filter out None values from normalization failures)
        normalized_models = [
            m for m in (normalize_alibaba_model(model) for model in response.data if model) if m
        ]

        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("alibaba", normalized_models)
        # Clear any previous quota error state on success
        _clear_alibaba_quota_error()

        logger.info(f"Fetched {len(normalized_models)} models from Alibaba Cloud")
        return normalized_models
    except QuotaExceededError:
        # Quota exceeded - cache the failure state to prevent repeated API calls
        _set_alibaba_quota_error()
        logger.warning(
            "Alibaba Cloud quota exceeded. Caching empty result for 900 seconds. "
            "Please check your plan and billing details."
        )
        return []
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        # Check if it's a 401 authentication error
        if "401" in error_msg or "Incorrect API key" in error_msg or "invalid_api_key" in error_msg:
            # Get the actual region being used
            region = getattr(Config, "ALIBABA_CLOUD_REGION", "international").lower()
            if region == "china":
                current_endpoint = "dashscope.aliyuncs.com (China/Beijing)"
                suggestion = (
                    "If your key only works for the International endpoint, set "
                    "ALIBABA_CLOUD_REGION='international' or provide "
                    "ALIBABA_CLOUD_API_KEY_INTERNATIONAL."
                )
            else:
                current_endpoint = "dashscope-intl.aliyuncs.com (International/Singapore)"
                suggestion = (
                    "If your key only works for the China endpoint, set "
                    "ALIBABA_CLOUD_REGION='china' or provide ALIBABA_CLOUD_API_KEY_CHINA."
                )

            logger.error(
                "Alibaba Cloud authentication failed (401): %s. "
                "Action required: Verify API key is valid and matches endpoint region. "
                "Currently using: %s. %s",
                error_msg,
                current_endpoint,
                suggestion,
            )
        elif (
            Config.ALIBABA_CLOUD_API_KEY
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None)
            or getattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None)
        ):
            logger.error("Failed to fetch models from Alibaba Cloud: %s", error_msg)
        else:
            logger.debug("Alibaba Cloud not available (no API key configured)")
        return []
