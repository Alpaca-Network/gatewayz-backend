"""Groq API client for direct inference.

Groq provides extremely fast inference with their LPU (Language Processing Unit)
hardware. This client routes requests directly to the Groq API instead of
through OpenRouter, enabling lower latency and direct access to Groq-specific
features.

API Documentation: https://console.groq.com/docs/api-reference
"""

import logging
from datetime import datetime, timezone

import httpx

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.circuit_breaker import CircuitBreakerConfig, CircuitBreakerError, get_circuit_breaker
from src.services.connection_pool import get_groq_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

# Circuit breaker configuration for Groq
# Groq is known for fast inference but can have rate limit issues
GROQ_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,  # Open after 5 consecutive failures
    success_threshold=2,  # Close after 2 consecutive successes
    timeout_seconds=60,  # Wait 60s before retrying
    failure_window_seconds=60,  # Measure failure rate over 60s
    failure_rate_threshold=0.5,  # Open if >50% failure rate
    min_requests_for_rate=10,  # Need at least 10 requests
)

# Modality constant
MODALITY_TEXT_TO_TEXT = "text->text"


def get_groq_client():
    """Get Groq client with connection pooling for better performance.

    Groq provides OpenAI-compatible API endpoints with ultra-fast inference
    powered by their custom LPU hardware.
    """
    try:
        if not Config.GROQ_API_KEY:
            raise ValueError("Groq API key not configured")

        # Use pooled client for better performance
        return get_groq_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        raise


def _make_groq_request_openai_internal(messages, model, **kwargs):
    """Internal function to make request to Groq (called by circuit breaker)."""
    from src.utils.provider_timing import ProviderTimingContext

    logger.info(f"Making Groq request with model: {model}")
    logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

    client = get_groq_client()

    with ProviderTimingContext("groq", model, "non_stream"):
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

    logger.info(f"Groq request successful for model: {model}")
    return response


def make_groq_request_openai(messages, model, **kwargs):
    """Make request to Groq using OpenAI-compatible client with circuit breaker protection.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'llama-3.3-70b-versatile', 'mixtral-8x7b-32768')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("groq", GROQ_CIRCUIT_CONFIG)

    try:
        response = circuit_breaker.call(
            _make_groq_request_openai_internal,
            messages,
            model,
            **kwargs
        )
        return response
    except CircuitBreakerError as e:
        logger.warning(f"Groq circuit breaker OPEN: {e.message}")
        capture_provider_error(
            e,
            provider='groq',
            model=model,
            endpoint='/chat/completions',
            extra_context={"circuit_breaker_state": e.state.value}
        )
        raise
    except Exception as e:
        try:
            logger.error(f"Groq request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Groq request failed (encoding error in logging)")
        capture_provider_error(e, provider='groq', model=model, endpoint='/chat/completions')
        raise


def _make_groq_request_openai_stream_internal(messages, model, **kwargs):
    """Internal function to make streaming request to Groq (called by circuit breaker)."""
    from src.utils.provider_timing import ProviderTimingContext

    logger.info(f"Making Groq streaming request with model: {model}")
    logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

    client = get_groq_client()

    with ProviderTimingContext("groq", model, "stream"):
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

    logger.info(f"Groq streaming request initiated for model: {model}")
    return stream


def make_groq_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Groq using OpenAI-compatible client with circuit breaker protection.

    Args:
        messages: List of message objects
        model: Model name to use (e.g., 'llama-3.3-70b-versatile', 'mixtral-8x7b-32768')
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("groq", GROQ_CIRCUIT_CONFIG)

    try:
        stream = circuit_breaker.call(
            _make_groq_request_openai_stream_internal,
            messages,
            model,
            **kwargs
        )
        return stream
    except CircuitBreakerError as e:
        logger.warning(f"Groq circuit breaker OPEN (streaming): {e.message}")
        capture_provider_error(
            e,
            provider='groq',
            model=model,
            endpoint='/chat/completions (stream)',
            extra_context={"circuit_breaker_state": e.state.value}
        )
        raise
    except Exception as e:
        try:
            logger.error(f"Groq streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Groq streaming request failed (encoding error in logging)")
        capture_provider_error(e, provider='groq', model=model, endpoint='/chat/completions (stream)')
        raise


def process_groq_response(response):
    """Process Groq response to extract relevant data.

    Groq returns OpenAI-compatible responses, so we use the same
    processing logic as other OpenAI-compatible providers.
    """
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
        logger.error(f"Failed to process Groq response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_groq_model(groq_model: dict) -> dict:
    """Normalize Groq catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = groq_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "groq", "raw_groq": groq_model or {}}

    slug = f"groq/{provider_model_id}"
    provider_slug = "groq"

    raw_display_name = (
        groq_model.get("display_name") or provider_model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = groq_model.get("owned_by")
    base_description = groq_model.get("description") or f"Groq hosted model {provider_model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Owned by {owned_by}."
    else:
        description = base_description

    metadata = groq_model.get("metadata") or {}
    hugging_face_id = metadata.get("huggingface_repo")

    context_length = metadata.get("context_length") or groq_model.get("context_length") or 0

    # Extract pricing information from API response
    pricing_info = groq_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Groq may return pricing in various formats
    # Check for token-based pricing (cents per token)
    if "cents_per_input_token" in pricing_info or "cents_per_output_token" in pricing_info:
        cents_input = pricing_info.get("cents_per_input_token", 0)
        cents_output = pricing_info.get("cents_per_output_token", 0)

        # Convert cents to dollars per token
        if cents_input:
            pricing["prompt"] = str(cents_input / 100)
        if cents_output:
            pricing["completion"] = str(cents_output / 100)

    # Check for direct dollar-based pricing
    elif "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])

    architecture = {
        "modality": metadata.get("modality", MODALITY_TEXT_TO_TEXT),
        "input_modalities": metadata.get("input_modalities") or ["text"],
        "output_modalities": metadata.get("output_modalities") or ["text"],
        "tokenizer": metadata.get("tokenizer"),
        "instruct_type": metadata.get("instruct_type"),
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": hugging_face_id,
        "name": display_name,
        "created": groq_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://groq.com",
        "model_logo_url": metadata.get("model_logo_url"),
        "source_gateway": "groq",
        "raw_groq": groq_model,
    }

    return enrich_model_with_pricing(normalized, "groq")


def fetch_models_from_groq():
    """Fetch models from Groq API with step-by-step logging"""
    from src.utils.step_logger import StepLogger
    from src.utils.provider_error_logging import (
        ProviderErrorType,
        ProviderFetchContext,
        log_provider_fetch_error,
        log_provider_fetch_success,
    )
    import time

    start_time = time.time()
    step_logger = StepLogger("Groq Model Fetch", total_steps=4)
    url = "https://api.groq.com/openai/v1/models"

    step_logger.start(provider="groq", endpoint=url)

    try:
        # Step 1: Validate API configuration
        step_logger.step(1, "Validating API configuration", provider="groq")

        if not Config.GROQ_API_KEY:
            error_msg = "Groq API key not configured"
            step_logger.failure(ValueError(error_msg))
            logger.error(f"[GROQ] {error_msg}")
            return None

        step_logger.success(status="configured")

        # Step 2: Fetch models from API
        step_logger.step(2, "Fetching models from API", endpoint=url)

        headers = {
            "Authorization": f"Bearer {Config.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        step_logger.success(raw_count=len(raw_models), status_code=response.status_code)

        # Step 3: Normalize and filter models
        step_logger.step(3, "Normalizing and filtering models", raw_count=len(raw_models))

        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_groq_model(model)]
            if norm_model is not None
        ]

        filtered_count = len(raw_models) - len(normalized_models)
        step_logger.success(normalized_count=len(normalized_models), filtered_count=filtered_count)

        # Step 4: Cache the models
        step_logger.step(4, "Caching models", cache_type="redis+local", model_count=len(normalized_models))

        cache_gateway_catalog("groq", normalized_models)
        step_logger.success(cached_count=len(normalized_models))

        # Complete with summary
        duration = time.time() - start_time
        step_logger.complete(total_models=len(normalized_models), duration_seconds=f"{duration:.2f}")

        # Log success with provider_error_logging utility
        log_provider_fetch_success(
            provider_slug="groq",
            models_count=len(normalized_models),
            duration=duration,
            additional_context={"endpoint": url, "raw_count": len(raw_models)},
        )

        return normalized_models

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="groq",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.API_TIMEOUT,
        )
        log_provider_fetch_error("groq", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("groq", normalize_groq_model, e)
        if fallback_models:
            cache_gateway_catalog("groq", fallback_models)
            return fallback_models

        return None

    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="groq",
            endpoint_url=url,
            status_code=e.response.status_code,
            duration=duration,
        )
        log_provider_fetch_error("groq", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("groq", normalize_groq_model, e)
        if fallback_models:
            cache_gateway_catalog("groq", fallback_models)
            return fallback_models

        return None

    except httpx.NetworkError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="groq",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.NETWORK_ERROR,
        )
        log_provider_fetch_error("groq", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("groq", normalize_groq_model, e)
        if fallback_models:
            cache_gateway_catalog("groq", fallback_models)
            return fallback_models

        return None

    except (ValueError, TypeError, KeyError) as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="groq",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.PARSING_ERROR,
        )
        log_provider_fetch_error("groq", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("groq", normalize_groq_model, e)
        if fallback_models:
            cache_gateway_catalog("groq", fallback_models)
            return fallback_models

        return None

    except Exception as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="groq", endpoint_url=url, duration=duration, error_type=ProviderErrorType.UNKNOWN
        )
        log_provider_fetch_error("groq", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("groq", normalize_groq_model, e)
        if fallback_models:
            cache_gateway_catalog("groq", fallback_models)
            return fallback_models

        return None
