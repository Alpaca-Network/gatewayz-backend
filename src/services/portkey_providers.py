"""
Provider Integration Functions

These functions fetch models from various AI providers using their native APIs.

PROVIDERS WITH DIRECT API INTEGRATION:
  - Cerebras: OpenAI-compatible API at https://api.cerebras.ai/v1/models
  - Nebius: OpenAI-compatible API at https://api.studio.nebius.ai/v1/models
  - xAI: OpenAI-compatible API at https://api.x.ai/v1/models
  - Novita: OpenAI-compatible API at https://api.novita.ai/v3/openai/models

PROVIDERS USING PORTKEY FILTERING:
  - Google: Filters Portkey catalog by patterns "@google/", "google/", "gemini", "gemma"
  - Hugging Face: Filters Portkey catalog by patterns "llava-hf", "hugging", "hf/"

HISTORICAL NOTE:
  Initially attempted to use pattern-based filtering from Portkey's unified catalog for all
  providers, but this approach was unreliable as Portkey's /v1/models endpoint doesn't always
  include models from all integrated providers. Direct API integration provides better
  reliability and completeness.
"""

import logging
from datetime import datetime, timezone

from src.cache import (
    _google_models_cache,
    _cerebras_models_cache,
    _nebius_models_cache,
    _xai_models_cache,
    _novita_models_cache,
    _huggingface_models_cache,
)
from src.services.pricing_lookup import enrich_model_with_pricing

logger = logging.getLogger(__name__)


def _filter_portkey_models_by_patterns(patterns: list, provider_name: str):
    """
    Filter Portkey unified models by name patterns and cache them.

    Args:
        patterns: List of strings to match in model ID (case-insensitive)
        provider_name: The internal provider name (e.g., "google", "cerebras")

    Returns:
        List of filtered models or None
    """
    try:
        from src.services.models import fetch_models_from_portkey

        logger.info(f"Fetching {provider_name} models from Portkey unified catalog (filtering by patterns: {patterns})")

        # Get all Portkey models
        all_portkey_models = fetch_models_from_portkey()

        if not all_portkey_models:
            logger.warning(f"No Portkey models returned for {provider_name}")
            return None

        logger.info(f"Portkey returned {len(all_portkey_models)} total models to filter for {provider_name}")

        # Filter by matching any of the patterns
        filtered_models = []
        seen_ids = set()  # Avoid duplicates

        for model in all_portkey_models:
            model_id = model.get("id", "").lower()

            # Check if any pattern matches
            for pattern in patterns:
                if pattern.lower() in model_id:
                    if model.get("id") not in seen_ids:
                        model_copy = model.copy()
                        model_copy["source_gateway"] = provider_name
                        filtered_models.append(model_copy)
                        seen_ids.add(model.get("id"))
                    break

        if filtered_models:
            logger.info(f"✅ Filtered {len(filtered_models)} {provider_name} models from Portkey catalog")
        else:
            logger.warning(f"⚠️  No {provider_name} models matched patterns {patterns} in Portkey catalog of {len(all_portkey_models)} models")
            # Log sample model IDs to help debug pattern matching
            if all_portkey_models:
                sample_ids = [m.get("id", "unknown") for m in all_portkey_models[:5]]
                logger.warning(f"Sample Portkey model IDs: {sample_ids}")

        return filtered_models if filtered_models else None

    except Exception as e:
        logger.error(f"Failed to filter {provider_name} models from Portkey: {e}", exc_info=True)
        return None


def fetch_models_from_google():
    """Fetch models from Google by filtering Portkey unified catalog"""
    try:
        # Google models use @google/ prefix in Portkey (also try without @ for compatibility)
        filtered_models = _filter_portkey_models_by_patterns(["@google/", "google/", "gemini", "gemma"], "google")

        if not filtered_models:
            logger.warning("No Google models found in Portkey catalog")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "google") for model in filtered_models if model]

        _google_models_cache["data"] = normalized_models
        _google_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Cached {len(normalized_models)} Google models from Portkey catalog")
        return _google_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from Google: {e}", exc_info=True)
        return None


def fetch_models_from_cerebras():
    """
    Fetch models from Cerebras API directly.

    Cerebras provides an OpenAI-compatible API at https://api.cerebras.ai/v1/models
    """
    try:
        from src.config import Config
        import httpx

        if not Config.CEREBRAS_API_KEY:
            logger.warning("Cerebras API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.CEREBRAS_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.cerebras.ai/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        if not raw_models:
            logger.warning("No models returned from Cerebras API")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "cerebras") for model in raw_models if model]

        _cerebras_models_cache["data"] = normalized_models
        _cerebras_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} Cerebras models from API")
        return _cerebras_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from Cerebras: {e}", exc_info=True)
        return None


def fetch_models_from_nebius():
    """
    Fetch models from Nebius API directly.

    Nebius provides an OpenAI-compatible API at https://api.studio.nebius.ai/v1/models
    """
    try:
        from src.config import Config
        import httpx

        if not Config.NEBIUS_API_KEY:
            logger.warning("Nebius API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.NEBIUS_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.studio.nebius.ai/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        if not raw_models:
            logger.warning("No models returned from Nebius API")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "nebius") for model in raw_models if model]

        _nebius_models_cache["data"] = normalized_models
        _nebius_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} Nebius models from API")
        return _nebius_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from Nebius: {e}", exc_info=True)
        return None


def fetch_models_from_xai():
    """
    Fetch models from xAI API directly.

    xAI provides an OpenAI-compatible API at https://api.x.ai/v1/models
    """
    try:
        from src.config import Config
        import httpx

        if not Config.XAI_API_KEY:
            logger.warning("xAI API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.XAI_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.x.ai/v1/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        if not raw_models:
            logger.warning("No models returned from xAI API")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "xai") for model in raw_models if model]

        _xai_models_cache["data"] = normalized_models
        _xai_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} xAI models from API")
        return _xai_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from xAI: {e}", exc_info=True)
        return None


def fetch_models_from_novita():
    """
    Fetch models from Novita API directly.

    Novita provides an OpenAI-compatible API at https://api.novita.ai/v3/openai/models
    """
    try:
        from src.config import Config
        import httpx

        if not Config.NOVITA_API_KEY:
            logger.warning("Novita API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.NOVITA_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get(
            "https://api.novita.ai/v3/openai/models",
            headers=headers,
            timeout=20.0,
        )
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", [])

        if not raw_models:
            logger.warning("No models returned from Novita API")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "novita") for model in raw_models if model]

        _novita_models_cache["data"] = normalized_models
        _novita_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Fetched {len(normalized_models)} Novita models from API")
        return _novita_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from Novita: {e}", exc_info=True)
        return None


def fetch_models_from_hug():
    """Fetch models from Hugging Face by filtering Portkey unified catalog"""
    try:
        # Hugging Face models include "llava-hf" and similar patterns
        filtered_models = _filter_portkey_models_by_patterns(["llava-hf", "hugging", "hf/"], "hug")

        if not filtered_models:
            logger.warning("No Hugging Face models found in Portkey catalog")
            return None

        normalized_models = [normalize_portkey_provider_model(model, "hug") for model in filtered_models if model]

        _huggingface_models_cache["data"] = normalized_models
        _huggingface_models_cache["timestamp"] = datetime.now(timezone.utc)

        logger.info(f"Cached {len(normalized_models)} Hugging Face models from Portkey catalog")
        return _huggingface_models_cache["data"]

    except Exception as e:
        logger.error(f"Failed to fetch models from Hugging Face: {e}", exc_info=True)
        return None


def normalize_portkey_provider_model(model: dict, provider: str) -> dict:
    """
    Normalize model from provider API to catalog schema.

    Used for both Portkey-filtered models and direct provider API responses.
    Model IDs are formatted as @provider/model-id for consistency across all providers.
    """
    try:
        model_id = model.get("id") or model.get("name", "")
        if not model_id:
            return {"source_gateway": provider, f"raw_{provider}": model}

        # Format: @provider/model-id (Portkey compatible format)
        # Check if model_id already has the @provider/ prefix to avoid duplication
        if model_id.startswith(f"@{provider}/"):
            slug = model_id
        else:
            slug = f"@{provider}/{model_id}"
        display_name = model.get("display_name") or model_id.replace("-", " ").replace("_", " ").title()
        description = model.get("description") or f"{provider.title()} hosted model: {model_id}"
        context_length = model.get("context_length") or 0

        pricing = {
            "prompt": None,
            "completion": None,
            "request": None,
            "image": None,
            "web_search": None,
            "internal_reasoning": None,
        }

        # Try to extract pricing if available
        if "pricing" in model:
            pricing_info = model.get("pricing", {})
            if isinstance(pricing_info, dict):
                pricing["prompt"] = pricing_info.get("prompt") or pricing_info.get("input")
                pricing["completion"] = pricing_info.get("completion") or pricing_info.get("output")

        architecture = {
            "modality": model.get("modality", "text->text"),
            "input_modalities": model.get("input_modalities") or ["text"],
            "output_modalities": model.get("output_modalities") or ["text"],
            "tokenizer": None,
            "instruct_type": None,
        }

        normalized = {
            "id": slug,
            "slug": slug,
            "canonical_slug": slug,
            "hugging_face_id": None,
            "name": display_name,
            "created": model.get("created"),
            "description": description,
            "context_length": context_length,
            "architecture": architecture,
            "pricing": pricing,
            "top_provider": None,
            "per_request_limits": None,
            "supported_parameters": model.get("supported_parameters") or [],
            "default_parameters": model.get("default_parameters") or {},
            "provider_slug": provider,
            "provider_site_url": None,
            "model_logo_url": None,
            "source_gateway": provider,
            f"raw_{provider}": model
        }

        return enrich_model_with_pricing(normalized, provider)

    except Exception as e:
        logger.error(f"Error normalizing {provider} model: {e}")
        return {"source_gateway": provider, f"raw_{provider}": model}
