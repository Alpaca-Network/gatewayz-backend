"""SambaNova model-catalog functions (fetch + normalize).

Minimal httpx fetcher for SambaNova's OpenAI-compatible ``/models`` endpoint,
following the pattern of the other ``<slug>_catalog.py`` modules. Unlike most
direct providers, SambaNova's ``/models`` response carries per-token USD
pricing (``pricing.prompt`` / ``pricing.completion``), which is used as-is.
"""

import logging

import httpx

from src.config import Config
from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

MODALITY_TEXT_TO_TEXT = "text->text"


def normalize_sambanova_model(sambanova_model: dict) -> dict | None:
    """Normalize SambaNova (Kimi) catalog entries to resemble the OpenRouter model shape."""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = sambanova_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "sambanova", "raw_sambanova": sambanova_model or {}}

    slug = f"sambanova/{provider_model_id}"
    provider_slug = "sambanova"

    display_name = clean_model_name(provider_model_id.replace("-", " ").replace("_", " ").title())
    description = f"SambaNova model {provider_model_id}."

    context_length = sambanova_model.get("context_length") or 0

    raw_pricing = sambanova_model.get("pricing") or {}
    pricing = {
        "prompt": raw_pricing.get("prompt"),
        "completion": raw_pricing.get("completion"),
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    input_modalities = ["text"]

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": input_modalities,
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
        "created": sambanova_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://sambanova.ai",
        "model_logo_url": None,
        "source_gateway": "sambanova",
        "raw_sambanova": sambanova_model,
    }

    return enrich_model_with_pricing(normalized, "sambanova")


def fetch_models_from_sambanova():
    """Fetch models from SambaNova's OpenAI-compatible API and normalize them."""
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.SAMBANOVA_API_KEY:
            logger.error("SambaNova API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.SAMBANOVA_API_KEY}",
            "Content-Type": "application/json",
        }

        url = "https://api.sambanova.ai/v1/models"
        logger.info("Fetching models from SambaNova API")

        response = httpx.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        logger.info(f"Fetched {len(raw_models)} models from SambaNova")

        normalized_models = [
            norm_model
            for model in raw_models
            if model
            for norm_model in [normalize_sambanova_model(model)]
            if norm_model is not None
        ]

        cache_gateway_catalog("sambanova", normalized_models)
        clear_gateway_error("sambanova")

        logger.info(f"Successfully cached {len(normalized_models)} SambaNova models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("SambaNova HTTP error: %s", error_msg)
        set_gateway_error("sambanova", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from SambaNova: %s", error_msg)
        set_gateway_error("sambanova", error_msg)
        return None
