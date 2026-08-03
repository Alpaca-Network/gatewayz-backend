"""Z.AI (Zhipu AI) model-catalog functions (fetch + normalize).

Moved verbatim from the former per-provider client module when its inference
trio was consolidated onto the OpenAI-compat adapter
(``src/services/providers/openai_compat.py`` / ``adapter_configs.py``).
Pricing extraction below is provider-specific and intentionally NOT unified
(see tests/services/test_provider_price_units.py).
"""

import logging

import httpx

from src.config import Config
from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Constants
MODALITY_TEXT_TO_TEXT = "text->text"


def normalize_zai_model(zai_model: dict) -> dict | None:
    """Normalize Z.AI catalog entries to resemble OpenRouter model shape.

    Z.AI provides GLM models (GLM-4.7, GLM-4.5-Air, etc.) with OpenAI-compatible format.
    """
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = zai_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "zai", "raw_zai": zai_model or {}}

    slug = f"zai/{provider_model_id}"
    provider_slug = "zai"

    raw_display_name = (
        zai_model.get("display_name")
        or zai_model.get("name")
        or provider_model_id.replace("-", " ").replace("_", " ").title()
    )
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)
    owned_by = zai_model.get("owned_by", "zai")
    base_description = zai_model.get("description") or f"Z.AI GLM model {provider_model_id}."
    if owned_by and owned_by.lower() not in base_description.lower():
        description = f"{base_description} Provided by Z.AI."
    else:
        description = base_description

    metadata = zai_model.get("metadata") or {}

    # Z.AI models typically have large context windows
    context_length = (
        metadata.get("context_length")
        or zai_model.get("context_length")
        or zai_model.get("context_window")
        or 128000  # Default for GLM models
    )

    # Z.AI pricing - check for various formats
    pricing_info = zai_model.get("pricing") or {}
    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    # Check for direct dollar-based pricing
    if "input" in pricing_info or "output" in pricing_info:
        if pricing_info.get("input"):
            pricing["prompt"] = str(pricing_info["input"])
        if pricing_info.get("output"):
            pricing["completion"] = str(pricing_info["output"])
    elif "prompt" in pricing_info or "completion" in pricing_info:
        if pricing_info.get("prompt"):
            pricing["prompt"] = str(pricing_info["prompt"])
        if pricing_info.get("completion"):
            pricing["completion"] = str(pricing_info["completion"])

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
        "hugging_face_id": None,
        "name": display_name,
        "created": zai_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": metadata.get("supported_parameters", []),
        "default_parameters": metadata.get("default_parameters", {}),
        "provider_slug": provider_slug,
        "provider_site_url": "https://z.ai",
        "model_logo_url": metadata.get("model_logo_url"),
        "source_gateway": "zai",
        "raw_zai": zai_model,
    }

    return enrich_model_with_pricing(normalized, "zai")


def fetch_models_from_zai():
    """Fetch models from Z.AI API and normalize to the catalog schema.

    Z.AI (Zhipu AI) provides the GLM model family with OpenAI-compatible API.
    """
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.ZAI_API_KEY:
            logger.error("Z.AI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.ZAI_API_KEY}",
            "Content-Type": "application/json",
        }

        # Z.AI uses OpenAI-compatible /models endpoint
        url = "https://api.z.ai/api/paas/v4/models"
        logger.info("Fetching models from Z.AI API")

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from Z.AI")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_zai_model(model)]
            if norm_model is not None
        ]

        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("zai", normalized_models)

        # Clear error state on successful fetch
        clear_gateway_error("zai")

        logger.info(f"Successfully cached {len(normalized_models)} Z.AI models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Z.AI HTTP error: %s", error_msg)
        set_gateway_error("zai", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Z.AI: %s", error_msg)
        set_gateway_error("zai", error_msg)
        return None
