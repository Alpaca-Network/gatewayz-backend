import logging

import httpx

from src.config import Config
from src.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerError,
    get_circuit_breaker,
)
from src.services.connection_pool import get_moonshot_pooled_client
from src.services.model_catalog_cache import cache_gateway_catalog
from src.services.providers.anthropic_transformer import extract_message_with_tools
from src.utils.model_name_validator import clean_model_name
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

# Circuit breaker configuration for Moonshot AI
MOONSHOT_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    success_threshold=2,
    timeout_seconds=60,
    failure_window_seconds=60,
    failure_rate_threshold=0.5,
    min_requests_for_rate=10,
)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"

# Static list of known Moonshot (Kimi) models — used as a description seed;
# the live catalog is populated from fetch_models_from_moonshot().
MOONSHOT_KNOWN_MODELS = [
    "moonshot-v1-8k",
    "moonshot-v1-32k",
    "moonshot-v1-128k",
    "kimi-k2-0711-preview",
]


def get_moonshot_client():
    """Get Moonshot AI (Kimi) client with connection pooling for better performance

    Moonshot AI provides OpenAI-compatible API endpoints for various Kimi models.
    """
    try:
        if not Config.MOONSHOT_API_KEY:
            raise ValueError("Moonshot API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_moonshot_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Moonshot client: {e}")
        raise


def _make_moonshot_request_openai_internal(messages, model, **kwargs):
    """Internal function to make request to Moonshot AI (called by circuit breaker)."""
    client = get_moonshot_client()
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return response


def make_moonshot_request_openai(messages, model, **kwargs):
    """Make request to Moonshot AI using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("moonshot", MOONSHOT_CIRCUIT_CONFIG)

    try:
        response = circuit_breaker.call(
            _make_moonshot_request_openai_internal, messages, model, **kwargs
        )
        return response
    except CircuitBreakerError as e:
        logger.warning(f"Moonshot circuit breaker OPEN: {e.message}")
        capture_provider_error(
            e,
            provider="moonshot",
            model=model,
            endpoint="/chat/completions",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"Moonshot request failed: {e}")
        capture_provider_error(e, provider="moonshot", model=model, endpoint="/chat/completions")
        raise


def _make_moonshot_request_openai_stream_internal(messages, model, **kwargs):
    """Internal function to make streaming request to Moonshot AI (called by circuit breaker)."""
    client = get_moonshot_client()
    stream = client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
    return stream


def make_moonshot_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Moonshot AI using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("moonshot", MOONSHOT_CIRCUIT_CONFIG)

    try:
        stream = circuit_breaker.call(
            _make_moonshot_request_openai_stream_internal, messages, model, **kwargs
        )
        return stream
    except CircuitBreakerError as e:
        logger.warning(f"Moonshot circuit breaker OPEN (streaming): {e.message}")
        capture_provider_error(
            e,
            provider="moonshot",
            model=model,
            endpoint="/chat/completions (stream)",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"Moonshot streaming request failed: {e}")
        capture_provider_error(
            e, provider="moonshot", model=model, endpoint="/chat/completions (stream)"
        )
        raise


def process_moonshot_response(response):
    """Process Moonshot response to extract relevant data"""
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
        logger.error(f"Failed to process Moonshot response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_moonshot_model(moonshot_model: dict) -> dict:
    """Normalize Moonshot catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = moonshot_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "moonshot", "raw_moonshot": moonshot_model or {}}

    slug = provider_model_id
    provider_slug = "moonshot"

    raw_display_name = (
        moonshot_model.get("display_name")
        or provider_model_id.replace("/", " / ").replace("-", " ").replace("_", " ").title()
    )
    display_name = clean_model_name(raw_display_name)
    owned_by = moonshot_model.get("owned_by") or "Moonshot AI"
    base_description = (
        moonshot_model.get("description") or f"Moonshot AI hosted model {provider_model_id}."
    )
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    context_length = moonshot_model.get("context_length", 0)

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    pricing_info = moonshot_model.get("pricing", {})
    if pricing_info:
        pricing["prompt"] = pricing_info.get("input")
        pricing["completion"] = pricing_info.get("output")

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": moonshot_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://www.moonshot.ai",
        "model_logo_url": None,
        "source_gateway": "moonshot",
        "raw_moonshot": moonshot_model,
    }

    return enrich_model_with_pricing(normalized, "moonshot")


def fetch_models_from_moonshot():
    """Fetch models from Moonshot AI's API with step-by-step logging"""
    import time

    from src.utils.provider_error_logging import (
        ProviderErrorType,
        ProviderFetchContext,
        log_provider_fetch_error,
        log_provider_fetch_success,
    )
    from src.utils.step_logger import StepLogger

    start_time = time.time()
    step_logger = StepLogger("Moonshot Model Fetch", total_steps=4)
    url = "https://api.moonshot.ai/v1/models"

    step_logger.start(provider="moonshot", endpoint=url)

    try:
        # Step 1: Validate API configuration
        step_logger.step(1, "Validating API configuration", provider="moonshot")

        if not Config.MOONSHOT_API_KEY:
            error_msg = "Moonshot API key not configured"
            step_logger.failure(ValueError(error_msg))
            logger.error(f"[MOONSHOT] {error_msg}")
            return None

        step_logger.success(status="configured")

        # Step 2: Fetch models from API
        step_logger.step(2, "Fetching models from API", endpoint=url)

        headers = {
            "Authorization": f"Bearer {Config.MOONSHOT_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        # Moonshot's /models endpoint returns an OpenAI-style {"data": [...]} envelope
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
            for norm_model in [normalize_moonshot_model(model)]
            if norm_model is not None
        ]

        filtered_count = len(raw_models) - len(normalized_models)
        step_logger.success(normalized_count=len(normalized_models), filtered_count=filtered_count)

        # Step 4: Cache the models
        step_logger.step(
            4, "Caching models", cache_type="redis+local", model_count=len(normalized_models)
        )

        cache_gateway_catalog("moonshot", normalized_models)
        step_logger.success(cached_count=len(normalized_models))

        duration = time.time() - start_time
        step_logger.complete(
            total_models=len(normalized_models), duration_seconds=f"{duration:.2f}"
        )

        log_provider_fetch_success(
            provider_slug="moonshot",
            models_count=len(normalized_models),
            duration=duration,
            additional_context={"endpoint": url, "raw_count": len(raw_models)},
        )

        return normalized_models

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="moonshot",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.API_TIMEOUT,
        )
        log_provider_fetch_error("moonshot", e, context)

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("moonshot", normalize_moonshot_model, e)
        if fallback_models:
            cache_gateway_catalog("moonshot", fallback_models)
            return fallback_models

        return None

    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="moonshot",
            endpoint_url=url,
            status_code=e.response.status_code,
            duration=duration,
        )
        log_provider_fetch_error("moonshot", e, context)

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("moonshot", normalize_moonshot_model, e)
        if fallback_models:
            cache_gateway_catalog("moonshot", fallback_models)
            return fallback_models

        return None

    except httpx.NetworkError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="moonshot",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.NETWORK_ERROR,
        )
        log_provider_fetch_error("moonshot", e, context)

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("moonshot", normalize_moonshot_model, e)
        if fallback_models:
            cache_gateway_catalog("moonshot", fallback_models)
            return fallback_models

        return None

    except (ValueError, TypeError, KeyError) as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="moonshot",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.PARSING_ERROR,
        )
        log_provider_fetch_error("moonshot", e, context)

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("moonshot", normalize_moonshot_model, e)
        if fallback_models:
            cache_gateway_catalog("moonshot", fallback_models)
            return fallback_models

        return None

    except Exception as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="moonshot",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.UNKNOWN,
        )
        log_provider_fetch_error("moonshot", e, context)

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("moonshot", normalize_moonshot_model, e)
        if fallback_models:
            cache_gateway_catalog("moonshot", fallback_models)
            return fallback_models

        return None
