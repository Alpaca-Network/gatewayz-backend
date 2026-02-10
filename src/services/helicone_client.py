import logging
from datetime import datetime, timezone

import httpx

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"

# Standard timeout for Helicone
HELICONE_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=60.0,
    write=10.0,
    pool=5.0,
)


def get_helicone_client():
    """Get Helicone AI Gateway client using OpenAI-compatible interface with connection pooling

    Helicone AI Gateway is an observability and monitoring platform that provides
    access to multiple AI providers with logging, caching, and analytics capabilities.

    Base URL: https://ai-gateway.helicone.ai/v1
    Documentation: https://docs.helicone.ai/gateway/overview
    """
    try:
        api_key = Config.HELICONE_API_KEY
        if not api_key:
            raise ValueError(
                "Helicone AI Gateway API key not configured. Please set HELICONE_API_KEY environment variable."
            )

        # Use connection pool with standard timeout
        return get_pooled_client(
            provider="helicone",
            base_url="https://ai-gateway.helicone.ai/v1",
            api_key=api_key,
            timeout=HELICONE_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Helicone AI Gateway client: {e}")
        raise


def make_helicone_request_openai(messages, model, **kwargs):
    """Make request to Helicone AI Gateway using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4o-mini", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_helicone_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Helicone AI Gateway request failed: {e}")
        raise


def make_helicone_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Helicone AI Gateway using OpenAI client

    Args:
        messages: List of message objects
        model: Model name (e.g., "gpt-4o-mini", "claude-3-sonnet")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_helicone_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Helicone AI Gateway streaming request failed: {e}")
        raise


def process_helicone_response(response):
    """Process Helicone AI Gateway response to extract relevant data"""
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
        logger.error(f"Failed to process Helicone AI Gateway response: {e}")
        raise


# Cache for Helicone model pricing from public API
_helicone_pricing_cache: dict = {"data": None, "timestamp": None}
HELICONE_PRICING_CACHE_TTL = 3600  # 1 hour


def fetch_helicone_pricing_from_public_api() -> dict | None:
    """Fetch pricing data from Helicone's public model registry API

    Returns:
        dict mapping model_id to pricing info, or None if fetch fails
    """
    import time

    # Check cache first
    if _helicone_pricing_cache["data"] is not None and _helicone_pricing_cache["timestamp"]:
        if time.time() - _helicone_pricing_cache["timestamp"] < HELICONE_PRICING_CACHE_TTL:
            return _helicone_pricing_cache["data"]

    try:
        response = httpx.get(
            "https://api.helicone.ai/v1/public/model-registry/models",
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.warning(f"Helicone public API returned status {response.status_code}")
            return None

        data = response.json()
        models = data.get("data", {}).get("models", [])

        pricing_map = {}
        for model in models:
            model_id = model.get("id")
            if not model_id:
                continue

            # Get endpoints for this model
            endpoints = model.get("endpoints", [])
            for endpoint in endpoints:
                # Use pricing from any endpoint (they should be consistent)
                pricing = endpoint.get("pricing", {})
                if pricing:
                    # Helicone returns pricing in per-1M format already
                    prompt_price = pricing.get("prompt", 0)
                    completion_price = pricing.get("completion", 0)

                    pricing_map[model_id] = {
                        "prompt": str(prompt_price),
                        "completion": str(completion_price),
                        "request": "0",
                        "image": "0",
                    }

                    # Also store common variations
                    if "/" not in model_id:
                        author = model.get("author", "")
                        if author:
                            pricing_map[f"{author}/{model_id}"] = pricing_map[model_id]
                    break

        # Update cache
        _helicone_pricing_cache["data"] = pricing_map
        _helicone_pricing_cache["timestamp"] = time.time()

        logger.info(f"Fetched pricing for {len(pricing_map)} models from Helicone public API")
        return pricing_map

    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.warning(f"Failed to fetch Helicone pricing from public API: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing Helicone pricing data: {e}")
        return None


def fetch_model_pricing_from_helicone(model_id: str):
    """Fetch pricing information for a specific model from Helicone AI Gateway

    Helicone AI Gateway routes requests to various providers (OpenAI, Anthropic, etc.)
    This function fetches pricing from Helicone's public model registry API.

    Args:
        model_id: Model identifier (e.g., "gpt-4o-mini", "claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens, or None if not available
    """
    try:
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return None to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Skipping pricing fetch for {model_id} (catalog building in progress)")
            return None

        # Fetch pricing from public API
        pricing_map = fetch_helicone_pricing_from_public_api()

        if pricing_map:
            # Try exact match first
            if model_id in pricing_map:
                return pricing_map[model_id]

            # Try without provider prefix
            model_name = model_id.split("/")[-1] if "/" in model_id else model_id
            if model_name in pricing_map:
                return pricing_map[model_name]

            # Try with common provider prefixes
            for prefix in ["anthropic", "openai", "google", "meta-llama"]:
                prefixed_id = f"{prefix}/{model_name}"
                if prefixed_id in pricing_map:
                    return pricing_map[prefixed_id]

        # Fallback: use provider-specific pricing lookup
        return get_provider_pricing_for_helicone_model(model_id)

    except Exception as e:
        logger.error(f"Failed to fetch pricing for Helicone model {model_id}: {e}")
        return None


def get_provider_pricing_for_helicone_model(model_id: str):
    """Get pricing for a Helicone model by looking up the underlying provider's pricing

    Helicone routes models to providers like OpenAI, Anthropic, etc.
    We can determine pricing by identifying the provider and looking up their rates.

    Args:
        model_id: Model identifier (e.g., "gpt-4o-mini", "claude-3-sonnet")

    Returns:
        dict with 'prompt' and 'completion' pricing per 1M tokens
    """
    try:
        # Cross-reference with known provider pricing from the system
        # This leverages the existing pricing infrastructure
        try:
            from src.services.models import _is_building_catalog
            from src.services.pricing import get_model_pricing

            # If we're building the catalog, return None to avoid circular dependency
            # The pricing will be populated in a later pass if needed
            if _is_building_catalog():
                logger.debug(
                    f"Skipping provider pricing lookup for {model_id} (catalog building in progress)"
                )
                return None

            # Try the full model ID first
            pricing = get_model_pricing(model_id)
            if pricing and pricing.get("found"):
                return {
                    "prompt": pricing.get("prompt", "0"),
                    "completion": pricing.get("completion", "0"),
                }

            # Try without the provider prefix
            model_name_only = model_id.split("/")[-1] if "/" in model_id else model_id
            pricing = get_model_pricing(model_name_only)
            if pricing and pricing.get("found"):
                return {
                    "prompt": pricing.get("prompt", "0"),
                    "completion": pricing.get("completion", "0"),
                }
        except ImportError:
            logger.debug("pricing module not available for cross-reference")

        return None

    except Exception as e:
        logger.debug(f"Failed to get provider pricing for {model_id}: {e}")
        return None


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_helicone_model(model) -> dict | None:
    """Normalize Helicone AI Gateway model to catalog schema

    Helicone models can originate from various providers (OpenAI, Anthropic, etc.)
    The gateway provides observability and monitoring on top of provider routing.
    Pricing is dynamically fetched from the underlying provider's pricing data.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    # Extract model ID
    model_id = getattr(model, "id", None)
    if not model_id:
        logger.warning("Helicone model missing 'id' field: %s", sanitize_for_logging(str(model)))
        return None

    # Determine provider from model ID
    # Models typically come in standard formats like "gpt-4o-mini", "claude-3-sonnet", etc.
    provider_slug = "helicone"
    raw_display_name = model_id

    # Try to detect provider from model name
    if "/" in model_id:
        provider_slug = model_id.split("/")[0]
        raw_display_name = model_id.split("/")[1]
    elif "gpt" in model_id.lower() or "o1" in model_id.lower():
        provider_slug = "openai"
    elif "claude" in model_id.lower():
        provider_slug = "anthropic"
    elif "gemini" in model_id.lower():
        provider_slug = "google"

    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    # Get description - Helicone doesn't provide this, so we create one
    description = (
        getattr(model, "description", None) or "Model available through Helicone AI Gateway"
    )

    # Get context length if available
    context_length = getattr(model, "context_length", 4096)

    # Get created date if available
    created = getattr(model, "created_at", None)

    # Fetch pricing dynamically from Helicone or underlying provider
    pricing = get_helicone_model_pricing(model_id)

    normalized = {
        "id": model_id,
        "slug": f"helicone/{model_id}",
        "canonical_slug": f"helicone/{model_id}",
        "hugging_face_id": None,
        "name": display_name,
        "created": created,
        "description": description,
        "context_length": context_length,
        "architecture": {
            "modality": MODALITY_TEXT_TO_TEXT,
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "instruct_type": "chat",
        },
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://www.helicone.ai",
        "model_logo_url": "https://www.helicone.ai/favicon.ico",
        "source_gateway": "helicone",
    }

    return enrich_model_with_pricing(normalized, "helicone")


def get_helicone_model_pricing(model_id: str) -> dict:
    """Get pricing for a Helicone AI Gateway model

    Fetches pricing from Helicone's public API or the underlying provider.
    Falls back to default zero pricing if unavailable.

    Args:
        model_id: Model identifier (e.g., "gpt-4o-mini")

    Returns:
        dict with 'prompt', 'completion', 'request', and 'image' pricing fields
    """
    try:
        # Fetch pricing from Helicone's public API (no circular dependency)
        pricing_map = fetch_helicone_pricing_from_public_api()

        if pricing_map:
            # Try exact match first
            if model_id in pricing_map:
                return {
                    "prompt": str(pricing_map[model_id].get("prompt", "0")),
                    "completion": str(pricing_map[model_id].get("completion", "0")),
                    "request": str(pricing_map[model_id].get("request", "0")),
                    "image": str(pricing_map[model_id].get("image", "0")),
                }

            # Try without provider prefix
            model_name = model_id.split("/")[-1] if "/" in model_id else model_id
            if model_name in pricing_map:
                return {
                    "prompt": str(pricing_map[model_name].get("prompt", "0")),
                    "completion": str(pricing_map[model_name].get("completion", "0")),
                    "request": str(pricing_map[model_name].get("request", "0")),
                    "image": str(pricing_map[model_name].get("image", "0")),
                }

            # Try with common provider prefixes
            for prefix in ["anthropic", "openai", "google", "meta-llama"]:
                prefixed_id = f"{prefix}/{model_name}"
                if prefixed_id in pricing_map:
                    return {
                        "prompt": str(pricing_map[prefixed_id].get("prompt", "0")),
                        "completion": str(pricing_map[prefixed_id].get("completion", "0")),
                        "request": str(pricing_map[prefixed_id].get("request", "0")),
                        "image": str(pricing_map[prefixed_id].get("image", "0")),
                    }

    except Exception as e:
        logger.debug(
            "Failed to fetch Helicone pricing for %s: %s",
            sanitize_for_logging(model_id),
            sanitize_for_logging(str(e)),
        )

    # Fallback: return default zero pricing
    return {
        "prompt": "0",
        "completion": "0",
        "request": "0",
        "image": "0",
    }


def fetch_models_from_helicone():
    """Fetch models from Helicone AI Gateway via OpenAI-compatible API

    Helicone AI Gateway provides access to models from multiple providers
    through a unified OpenAI-compatible endpoint with observability features.
    """
    try:
        # Check if API key is configured
        if not Config.HELICONE_API_KEY:
            logger.warning("Helicone API key not configured - skipping model fetch")
            return []

        client = get_helicone_client()
        response = client.models.list()

        if not response or not hasattr(response, "data"):
            logger.warning("No models returned from Helicone AI Gateway")
            return []

        # Normalize models and filter out None (models without pricing)
        normalized_models = [
            m for m in (normalize_helicone_model(model) for model in response.data if model) if m
        ]

        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("helicone", normalized_models)

        logger.info(f"Fetched {len(normalized_models)} models from Helicone AI Gateway")
        return normalized_models
    except Exception as e:
        logger.error(
            "Failed to fetch models from Helicone AI Gateway: %s", sanitize_for_logging(str(e))
        )
        return []
