"""MiniMax model-catalog functions (fetch + normalize).

Minimal httpx fetcher for MiniMax's OpenAI-compatible ``/models`` endpoint,
following the pattern of the other ``<slug>_catalog.py`` modules (e.g.
``zai_catalog.py``). MiniMax's ``/models`` response does not include
pricing or context_length, so both are left conservatively unset / defaulted
rather than guessed — the pricing pipeline gates unpriced models
automatically (see ``src/services/pricing/pricing_lookup.py``).
"""

import logging

import httpx

from src.config import Config
from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

MODALITY_TEXT_TO_TEXT = "text->text"


def normalize_minimax_model(minimax_model: dict) -> dict | None:
    """Normalize MiniMax catalog entries to resemble the OpenRouter model shape."""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = minimax_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "minimax", "raw_minimax": minimax_model or {}}

    slug = f"minimax/{provider_model_id}"
    provider_slug = "minimax"

    display_name = clean_model_name(
        provider_model_id.replace("-", " ").replace("_", " ").title()
    )
    owned_by = minimax_model.get("owned_by", "minimax")
    description = f"MiniMax model {provider_model_id}, provided by {owned_by}."

    # MiniMax's /models endpoint does not return context_length or pricing.
    context_length = minimax_model.get("context_length") or 0

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
        "provider_site_url": "https://www.minimax.io",
        "model_logo_url": None,
        "source_gateway": "minimax",
        "raw_minimax": minimax_model,
    }

    return enrich_model_with_pricing(normalized, "minimax")


def fetch_models_from_minimax():
    """Fetch models from MiniMax's OpenAI-compatible API and normalize them."""
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.MINIMAX_API_KEY:
            logger.error("MiniMax API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.MINIMAX_API_KEY}",
            "Content-Type": "application/json",
        }

        url = "https://api.minimax.io/v1/models"
        logger.info("Fetching models from MiniMax API")

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from MiniMax")

        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_minimax_model(model)]
            if norm_model is not None
        ]

        cache_gateway_catalog("minimax", normalized_models)
        clear_gateway_error("minimax")

        logger.info(f"Successfully cached {len(normalized_models)} MiniMax models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("MiniMax HTTP error: %s", error_msg)
        set_gateway_error("minimax", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from MiniMax: %s", error_msg)
        set_gateway_error("minimax", error_msg)
        return None
