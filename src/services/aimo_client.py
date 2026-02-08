import logging
from datetime import datetime, timezone

import httpx
from openai import OpenAI

from src.services.model_catalog_cache import (
    cache_gateway_catalog,
    get_cached_gateway_catalog,
    clear_gateway_error,
    is_gateway_in_error_state,
    set_gateway_error,
)
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_aimo_client():
    """Get AIMO Network client using OpenAI-compatible interface

    AIMO Network is a decentralized AI marketplace providing OpenAI-compatible API endpoints
    Base URL: https://beta.aimo.network/api/v1/
    """
    try:
        if not Config.AIMO_API_KEY:
            raise ValueError("AIMO API key not configured")

        return OpenAI(base_url="https://beta.aimo.network/api/v1", api_key=Config.AIMO_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize AIMO client: {e}")
        raise


def make_aimo_request_openai(messages, model, **kwargs):
    """Make request to AIMO Network using OpenAI client

    Args:
        messages: List of message objects
        model: Model name in format "provider_pubkey:model_name"
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_aimo_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"AIMO request failed: {e}")
        raise


def make_aimo_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to AIMO Network using OpenAI client

    Args:
        messages: List of message objects
        model: Model name in format "provider_pubkey:model_name"
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_aimo_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"AIMO streaming request failed: {e}")
        raise


def process_aimo_response(response):
    """Process AIMO response to extract relevant data"""
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
        logger.error(f"Failed to process AIMO response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def normalize_aimo_model(aimo_model: dict) -> dict:
    """Normalize AIMO catalog entries to resemble OpenRouter model shape

    AIMO models use format: provider_pubkey:model_name
    Model data structure:
    - name: base model name (e.g., "DeepSeek-V3-1")
    - display_name: human-readable name
    - providers: list of provider objects with id, name, and pricing
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_name = aimo_model.get("name")
    if not model_name:
        logger.warning("AIMO model missing 'name' field: %s", sanitize_for_logging(str(aimo_model)))
        return None

    # Normalize model name by stripping common provider prefixes
    # AIMO may return model names like "google/gemini-2.5-pro" or just "gemini-2.5-pro"
    model_name_normalized = model_name
    provider_prefixes = ["google/", "openai/", "anthropic/", "meta/", "meta-llama/", "mistralai/"]
    for prefix in provider_prefixes:
        if model_name.lower().startswith(prefix):
            model_name_normalized = model_name[len(prefix) :]
            break

    # Get provider information (use first provider if multiple)
    providers = aimo_model.get("providers", [])
    if not providers:
        logger.warning("AIMO model '%s' has no providers", sanitize_for_logging(model_name))
        return None

    # For now, use the first provider
    provider = providers[0]
    provider_id = provider.get("id")
    provider_name = provider.get("name", "unknown")

    # Create user-friendly model ID in format: aimo/model_name
    # Use the normalized model name (without provider prefix) for consistency
    # Store the original AIMO format (provider_pubkey:model_name) in raw metadata
    original_aimo_id = f"{provider_id}:{model_name}"
    model_id = f"aimo/{model_name_normalized}"

    slug = model_id
    # Always use "aimo" as the provider slug for AIMO Network models
    provider_slug = "aimo"

    # Create canonical slug from the base model name (without the provider prefix)
    # This allows the model to be grouped with same models from other providers
    canonical_slug = model_name_normalized.lower()

    # Get display name from API or generate from model name
    raw_display_name = aimo_model.get("display_name") or model_name_normalized.replace("-", " ").title()
    # Clean malformed model names (remove company prefix with colon, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    base_description = (
        f"AIMO Network decentralized model {model_name_normalized} provided by {provider_name}."
    )
    description = base_description

    context_length = aimo_model.get("context_length", 0)

    # Extract pricing from provider object
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # AIMO provider pricing
    provider_pricing = provider.get("pricing", {})
    if provider_pricing:
        prompt_price = provider_pricing.get("prompt")
        completion_price = provider_pricing.get("completion")
        # Convert to string if not None
        pricing["prompt"] = str(prompt_price) if prompt_price is not None else None
        pricing["completion"] = str(completion_price) if completion_price is not None else None

    # Extract architecture from AIMO model
    aimo_arch = aimo_model.get("architecture", {})
    input_modalities = aimo_arch.get("input_modalities", ["text"])
    output_modalities = aimo_arch.get("output_modalities", ["text"])

    # Determine modality string
    if input_modalities == ["text"] and output_modalities == ["text"]:
        modality = MODALITY_TEXT_TO_TEXT
    else:
        modality = "multimodal"

    architecture = {
        "modality": modality,
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": canonical_slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": aimo_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://aimo.network",
        "model_logo_url": None,
        "source_gateway": "aimo",
        "raw_aimo": aimo_model,
        "aimo_native_id": original_aimo_id,  # Store original AIMO format for routing
    }

    return enrich_model_with_pricing(normalized, "aimo")


def fetch_models_from_aimo():
    """Fetch models from AIMO Network API with resilience features

    Hardening measures:
    - Configurable, shorter timeouts (5s fetch, 3s connect) to prevent thread pool blocking
    - Retry logic with exponential backoff across multiple base URLs
    - Optional HTTP fallback for HTTPS failures
    - Stale-while-revalidate caching to serve cached data on failure
    - Non-blocking: failures don't block the thread pool during parallel catalog builds
    - Circuit breaker: skip fetch attempts when gateway is in error state

    Note: AIMO is a decentralized AI marketplace with OpenAI-compatible API.
    Models are fetched from the marketplace endpoint if available.
    """
    if not Config.AIMO_API_KEY:
        logger.debug("AIMO API key not configured, using cached data if available")
        return get_cached_gateway_catalog("aimo") or []

    # Circuit breaker: skip fetch if gateway is in error state (exponential backoff)
    if is_gateway_in_error_state("aimo"):
        cached_data = get_cached_gateway_catalog("aimo") or []
        if cached_data:
            logger.debug("AIMO gateway in error state, returning cached data (%d models)", len(cached_data))
            return cached_data
        logger.debug("AIMO gateway in error state, no cached data available")
        return []

    headers = {
        "Authorization": f"Bearer {Config.AIMO_API_KEY}",
        "Content-Type": "application/json",
    }

    # Build list of URLs to try, starting with HTTPS (primary and fallback)
    urls_to_try = [f"{base_url}/models" for base_url in Config.AIMO_BASE_URLS]

    # Add HTTP fallback URLs if enabled
    if Config.AIMO_ENABLE_HTTP_FALLBACK:
        http_urls = [
            url.replace("https://", "http://") for url in urls_to_try if url.startswith("https://")
        ]
        urls_to_try.extend(http_urls)

    # Create timeout with separate connect and read timeouts
    timeout_config = httpx.Timeout(
        timeout=Config.AIMO_FETCH_TIMEOUT,
        connect=Config.AIMO_CONNECT_TIMEOUT,
    )

    last_error = None
    for attempt in range(Config.AIMO_MAX_RETRIES + 1):
        for url_idx, url in enumerate(urls_to_try):
            try:
                logger.debug(
                    f"AIMO fetch attempt {attempt + 1}/{Config.AIMO_MAX_RETRIES + 1}, "
                    f"URL {url_idx + 1}/{len(urls_to_try)}: {url}"
                )

                response = httpx.get(
                    url, headers=headers, timeout=timeout_config, follow_redirects=True
                )
                response.raise_for_status()

                payload = response.json()
                raw_models = payload.get("data", [])

                if not raw_models:
                    logger.warning("No models returned from AIMO API at %s", url)
                    continue

                # Normalize models and filter out None values (models without providers)
                normalized_models = [
                    normalized
                    for model in raw_models
                    if model and (normalized := normalize_aimo_model(model)) is not None
                ]

                # Deduplicate models by canonical_slug (same model from different AIMO providers)
                # Keep only the first occurrence of each unique model
                seen_models = {}
                deduplicated_models = []
                for model in normalized_models:
                    canonical_slug = model.get("canonical_slug")
                    if canonical_slug and canonical_slug not in seen_models:
                        seen_models[canonical_slug] = True
                        deduplicated_models.append(model)
                    elif not canonical_slug:
                        # If no canonical slug, keep it (shouldn't happen but be safe)
                        deduplicated_models.append(model)

                logger.info(
                    f"Fetched {len(normalized_models)} AIMO models from {url}, "
                    f"deduplicated to {len(deduplicated_models)} unique models"
                )

                # Cache models in Redis with automatic TTL and error tracking
                cache_gateway_catalog("aimo", deduplicated_models)

                return deduplicated_models

            except httpx.TimeoutException:
                last_error = f"Timeout at {url} after {Config.AIMO_FETCH_TIMEOUT}s"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning("AIMO timeout: %s (final attempt)", last_error)
                else:
                    logger.debug("AIMO timeout: %s (attempt %d)", last_error, attempt + 1)
                continue

            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code} at {url}"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning(
                        "AIMO HTTP error: %s - %s (final attempt)",
                        last_error,
                        sanitize_for_logging(e.response.text[:200]),
                    )
                else:
                    logger.debug(
                        "AIMO HTTP error: %s - %s (attempt %d)",
                        last_error,
                        sanitize_for_logging(e.response.text[:200]),
                        attempt + 1,
                    )
                continue

            except Exception as e:
                last_error = f"Error at {url}: {sanitize_for_logging(str(e))}"
                # Use debug for intermediate retries, warning only on final attempt
                if attempt == Config.AIMO_MAX_RETRIES:
                    logger.warning("AIMO fetch error: %s (final attempt)", last_error)
                else:
                    logger.debug("AIMO fetch error: %s (attempt %d)", last_error, attempt + 1)
                continue

    # All retries exhausted - use stale cache if available (stale-while-revalidate)
    cached_data = get_cached_gateway_catalog("aimo") or []
    if cached_data:
        logger.warning(
            "AIMO fetch failed after all retries, returning stale cached data. Last error: %s",
            last_error,
        )
        set_gateway_error("aimo", f"Using stale cache: {last_error}")
        return cached_data

    # No cached data available
    error_msg = last_error or "Unknown error during AIMO fetch"
    logger.error("Failed to fetch models from AIMO after all retries: %s", error_msg)
    set_gateway_error("aimo", error_msg)
    return []
