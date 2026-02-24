import logging

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerError,
    get_circuit_breaker,
)
from src.services.connection_pool import get_together_pooled_client
from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

# Circuit breaker configuration for Together.ai
TOGETHER_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    success_threshold=2,
    timeout_seconds=60,
    failure_window_seconds=60,
    failure_rate_threshold=0.5,
    min_requests_for_rate=10,
)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_together_client():
    """Get Together.ai client with connection pooling for better performance

    Together.ai provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.TOGETHER_API_KEY:
            raise ValueError("Together API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_together_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Together client: {e}")
        raise


def _make_together_request_openai_internal(messages, model, **kwargs):
    """Internal function to make request to Together.ai (called by circuit breaker)."""
    client = get_together_client()
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return response


def make_together_request_openai(messages, model, **kwargs):
    """Make request to Together.ai using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("together", TOGETHER_CIRCUIT_CONFIG)

    try:
        response = circuit_breaker.call(
            _make_together_request_openai_internal, messages, model, **kwargs
        )
        return response
    except CircuitBreakerError as e:
        logger.warning(f"Together circuit breaker OPEN: {e.message}")
        capture_provider_error(
            e,
            provider="together",
            model=model,
            endpoint="/chat/completions",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"Together request failed: {e}")
        capture_provider_error(e, provider="together", model=model, endpoint="/chat/completions")
        raise


def _make_together_request_openai_stream_internal(messages, model, **kwargs):
    """Internal function to make streaming request to Together.ai (called by circuit breaker)."""
    client = get_together_client()
    stream = client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
    return stream


def make_together_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Together.ai using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("together", TOGETHER_CIRCUIT_CONFIG)

    try:
        stream = circuit_breaker.call(
            _make_together_request_openai_stream_internal, messages, model, **kwargs
        )
        return stream
    except CircuitBreakerError as e:
        logger.warning(f"Together circuit breaker OPEN (streaming): {e.message}")
        capture_provider_error(
            e,
            provider="together",
            model=model,
            endpoint="/chat/completions (stream)",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"Together streaming request failed: {e}")
        capture_provider_error(
            e, provider="together", model=model, endpoint="/chat/completions (stream)"
        )
        raise


def process_together_response(response):
    """Process Together response to extract relevant data"""
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process Together response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_together_model(together_model: dict) -> dict:
    """Normalize Together catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = together_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "together", "raw_together": together_model or {}}

    slug = provider_model_id
    provider_slug = "together"

    # Get display name from API or generate from model ID
    raw_display_name = (
        together_model.get("display_name")
        or provider_model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove parentheses with size info, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = together_model.get("owned_by") or together_model.get("organization")
    base_description = (
        together_model.get("description") or f"Together hosted model {provider_model_id}."
    )
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    context_length = together_model.get("context_length", 0)

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Extract pricing if available
    pricing_info = together_model.get("pricing", {})
    if pricing_info:
        pricing["prompt"] = pricing_info.get("input")
        pricing["completion"] = pricing_info.get("output")

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": together_model.get("config", {}).get("tokenizer"),
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": together_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://together.ai",
        "model_logo_url": None,
        "source_gateway": "together",
        "raw_together": together_model,
    }

    return enrich_model_with_pricing(normalized, "together")


def fetch_models_from_together():
    """Fetch models from Together.ai API with step-by-step logging"""
    import time

    from src.utils.provider_error_logging import (
        ProviderErrorType,
        ProviderFetchContext,
        log_provider_fetch_error,
        log_provider_fetch_success,
    )
    from src.utils.step_logger import StepLogger

    start_time = time.time()
    step_logger = StepLogger("Together Model Fetch", total_steps=4)
    url = "https://api.together.xyz/v1/models"

    step_logger.start(provider="together", endpoint=url)

    try:
        # Step 1: Validate API configuration
        step_logger.step(1, "Validating API configuration", provider="together")

        if not Config.TOGETHER_API_KEY:
            error_msg = "Together API key not configured"
            step_logger.failure(ValueError(error_msg))
            logger.error(f"[TOGETHER] {error_msg}")
            return None

        step_logger.success(status="configured")

        # Step 2: Fetch models from API
        step_logger.step(2, "Fetching models from API", endpoint=url)

        headers = {
            "Authorization": f"Bearer {Config.TOGETHER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        # Together API returns a list directly, not wrapped in {"data": [...]}
        raw_models = payload if isinstance(payload, list) else payload.get("data", [])

        step_logger.success(
            raw_count=len(raw_models),
            status_code=response.status_code,
            response_type=type(payload).__name__,
        )

        # Step 3: Normalize and filter models
        step_logger.step(3, "Normalizing and filtering models", raw_count=len(raw_models))

        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_together_model(model)]
            if norm_model is not None
        ]

        filtered_count = len(raw_models) - len(normalized_models)
        step_logger.success(normalized_count=len(normalized_models), filtered_count=filtered_count)

        # Step 4: Cache the models
        step_logger.step(
            4, "Caching models", cache_type="redis+local", model_count=len(normalized_models)
        )

        cache_gateway_catalog("together", normalized_models)
        step_logger.success(cached_count=len(normalized_models))

        # Complete with summary
        duration = time.time() - start_time
        step_logger.complete(
            total_models=len(normalized_models), duration_seconds=f"{duration:.2f}"
        )

        # Log success with provider_error_logging utility
        log_provider_fetch_success(
            provider_slug="together",
            models_count=len(normalized_models),
            duration=duration,
            additional_context={"endpoint": url, "raw_count": len(raw_models)},
        )

        return normalized_models

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="together",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.API_TIMEOUT,
        )
        log_provider_fetch_error("together", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("together", normalize_together_model, e)
        if fallback_models:
            cache_gateway_catalog("together", fallback_models)
            return fallback_models

        return None

    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="together",
            endpoint_url=url,
            status_code=e.response.status_code,
            duration=duration,
        )
        log_provider_fetch_error("together", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("together", normalize_together_model, e)
        if fallback_models:
            cache_gateway_catalog("together", fallback_models)
            return fallback_models

        return None

    except httpx.NetworkError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="together",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.NETWORK_ERROR,
        )
        log_provider_fetch_error("together", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("together", normalize_together_model, e)
        if fallback_models:
            cache_gateway_catalog("together", fallback_models)
            return fallback_models

        return None

    except (ValueError, TypeError, KeyError) as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="together",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.PARSING_ERROR,
        )
        log_provider_fetch_error("together", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("together", normalize_together_model, e)
        if fallback_models:
            cache_gateway_catalog("together", fallback_models)
            return fallback_models

        return None

    except Exception as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="together",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.UNKNOWN,
        )
        log_provider_fetch_error("together", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("together", normalize_together_model, e)
        if fallback_models:
            cache_gateway_catalog("together", fallback_models)
            return fallback_models

        return None
