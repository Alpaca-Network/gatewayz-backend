import logging

from src.config import Config
from src.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerError,
    get_circuit_breaker,
)
from src.services.connection_pool import get_minimax_pooled_client
from src.services.model_catalog_cache import cache_gateway_catalog
from src.services.providers.anthropic_transformer import extract_message_with_tools
from src.utils.sentry_context import capture_provider_error

# Initialize logging
logger = logging.getLogger(__name__)

# Circuit breaker configuration for MiniMax
MINIMAX_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    success_threshold=2,
    timeout_seconds=60,
    failure_window_seconds=60,
    failure_rate_threshold=0.5,
    min_requests_for_rate=10,
)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"

# MiniMax does not expose a reliable /models discovery endpoint, so the
# catalog is seeded from this static, hand-maintained list instead of a
# live HTTP fetch (see fetch_models_from_minimax below).
MINIMAX_KNOWN_MODELS = [
    "MiniMax-M1",
    "MiniMax-Text-01",
]


def get_minimax_client():
    """Get MiniMax client with connection pooling for better performance

    MiniMax exposes an OpenAI-compatible chat completions endpoint
    (https://api.minimaxi.com/v1/text/chatcompletion_v2 under the hood, via
    the OpenAI SDK's base_url + /chat/completions convention).
    """
    try:
        if not Config.MINIMAX_API_KEY:
            raise ValueError("MiniMax API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_minimax_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize MiniMax client: {e}")
        raise


def _make_minimax_request_openai_internal(messages, model, **kwargs):
    """Internal function to make request to MiniMax (called by circuit breaker)."""
    client = get_minimax_client()
    response = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return response


def make_minimax_request_openai(messages, model, **kwargs):
    """Make request to MiniMax using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("minimax", MINIMAX_CIRCUIT_CONFIG)

    try:
        response = circuit_breaker.call(
            _make_minimax_request_openai_internal, messages, model, **kwargs
        )
        return response
    except CircuitBreakerError as e:
        logger.warning(f"MiniMax circuit breaker OPEN: {e.message}")
        capture_provider_error(
            e,
            provider="minimax",
            model=model,
            endpoint="/chat/completions",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"MiniMax request failed: {e}")
        capture_provider_error(e, provider="minimax", model=model, endpoint="/chat/completions")
        raise


def _make_minimax_request_openai_stream_internal(messages, model, **kwargs):
    """Internal function to make streaming request to MiniMax (called by circuit breaker)."""
    client = get_minimax_client()
    stream = client.chat.completions.create(model=model, messages=messages, stream=True, **kwargs)
    return stream


def make_minimax_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to MiniMax using OpenAI client with circuit breaker protection

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    circuit_breaker = get_circuit_breaker("minimax", MINIMAX_CIRCUIT_CONFIG)

    try:
        stream = circuit_breaker.call(
            _make_minimax_request_openai_stream_internal, messages, model, **kwargs
        )
        return stream
    except CircuitBreakerError as e:
        logger.warning(f"MiniMax circuit breaker OPEN (streaming): {e.message}")
        capture_provider_error(
            e,
            provider="minimax",
            model=model,
            endpoint="/chat/completions (stream)",
            extra_context={"circuit_breaker_state": e.state.value},
        )
        raise
    except Exception as e:
        logger.error(f"MiniMax streaming request failed: {e}")
        capture_provider_error(
            e, provider="minimax", model=model, endpoint="/chat/completions (stream)"
        )
        raise


def process_minimax_response(response):
    """Process MiniMax response to extract relevant data"""
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
        logger.error(f"Failed to process MiniMax response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_minimax_model(minimax_model: dict) -> dict:
    """Normalize a static MiniMax model entry to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = minimax_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "minimax", "raw_minimax": minimax_model or {}}

    slug = provider_model_id
    provider_slug = "minimax"

    display_name = minimax_model.get("display_name") or provider_model_id
    description = minimax_model.get("description") or (
        f"MiniMax hosted model {provider_model_id}."
    )
    context_length = minimax_model.get("context_length", 0)

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

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
        "created": minimax_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://www.minimaxi.com",
        "model_logo_url": None,
        "source_gateway": "minimax",
        "raw_minimax": minimax_model,
    }

    return enrich_model_with_pricing(normalized, "minimax")


def fetch_models_from_minimax():
    """Return MiniMax's catalog from a static, hand-maintained model list.

    MiniMax does not expose a reliable OpenAI-style ``/models`` discovery
    endpoint, so — unlike most direct providers — this does not make an
    HTTP call. It mirrors the fetch_models_from_<provider>() contract
    (logging, caching, DB fallback) so it plugs into the same sync pipeline.
    """
    import time

    from src.utils.provider_error_logging import log_provider_fetch_success
    from src.utils.step_logger import StepLogger

    start_time = time.time()
    step_logger = StepLogger("MiniMax Model Fetch", total_steps=3)

    step_logger.start(provider="minimax", endpoint="static:MINIMAX_KNOWN_MODELS")

    try:
        # Step 1: Validate API configuration
        step_logger.step(1, "Validating API configuration", provider="minimax")

        if not Config.MINIMAX_API_KEY:
            error_msg = "MiniMax API key not configured"
            step_logger.failure(ValueError(error_msg))
            logger.error(f"[MINIMAX] {error_msg}")
            return None

        step_logger.success(status="configured")

        # Step 2: Build the static model list
        step_logger.step(2, "Building static model list", model_count=len(MINIMAX_KNOWN_MODELS))

        raw_models = [{"id": model_id} for model_id in MINIMAX_KNOWN_MODELS]

        step_logger.success(raw_count=len(raw_models))

        # Step 3: Normalize and cache
        step_logger.step(3, "Normalizing and caching models", raw_count=len(raw_models))

        normalized_models = [normalize_minimax_model(model) for model in raw_models]
        cache_gateway_catalog("minimax", normalized_models)

        step_logger.success(cached_count=len(normalized_models))

        duration = time.time() - start_time
        step_logger.complete(
            total_models=len(normalized_models), duration_seconds=f"{duration:.2f}"
        )

        log_provider_fetch_success(
            provider_slug="minimax",
            models_count=len(normalized_models),
            duration=duration,
            additional_context={"endpoint": "static:MINIMAX_KNOWN_MODELS"},
        )

        return normalized_models

    except Exception as e:
        step_logger.failure(e)
        logger.error(f"[MINIMAX] Failed to build static model list: {e}")

        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("minimax", normalize_minimax_model, e)
        if fallback_models:
            cache_gateway_catalog("minimax", fallback_models)
            return fallback_models

        return None
