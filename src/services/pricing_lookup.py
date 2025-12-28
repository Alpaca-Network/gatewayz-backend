"""
Pricing Lookup Service
Provides manual pricing lookup for providers that don't expose pricing via API
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Gateway providers that route to underlying providers (OpenAI, Anthropic, etc.)
# These need cross-reference pricing from OpenRouter if no manual pricing exists
GATEWAY_PROVIDERS = {
    "aihubmix",
    "anannas",
    "helicone",
    "vercel-ai-gateway",
}

# Cache for pricing data
_pricing_cache: dict[str, Any] | None = None


def load_manual_pricing() -> dict[str, Any]:
    """Load manual pricing data from JSON file"""
    global _pricing_cache

    if _pricing_cache is not None:
        return _pricing_cache

    try:
        pricing_file = Path(__file__).parent.parent / "data" / "manual_pricing.json"

        if not pricing_file.exists():
            logger.warning(f"Manual pricing file not found: {pricing_file}")
            return {}

        with open(pricing_file) as f:
            _pricing_cache = json.load(f)

        logger.info(f"Loaded manual pricing data for {len(_pricing_cache) - 1} providers")
        return _pricing_cache

    except Exception as e:
        logger.error(f"Failed to load manual pricing: {e}")
        return {}


def get_model_pricing(gateway: str, model_id: str) -> dict[str, str] | None:
    """
    Get pricing for a specific model from manual pricing data

    Args:
        gateway: Gateway name (e.g., 'deepinfra', 'featherless', 'chutes')
        model_id: Model ID (e.g., 'meta-llama/Meta-Llama-3.1-8B-Instruct')

    Returns:
        Pricing dictionary or None if not found
    """
    try:
        pricing_data = load_manual_pricing()

        if not pricing_data:
            return None

        gateway_lower = gateway.lower()

        if gateway_lower not in pricing_data:
            return None

        gateway_pricing = pricing_data[gateway_lower]

        if model_id in gateway_pricing:
            return gateway_pricing[model_id]

        # Try case-insensitive match
        for key, value in gateway_pricing.items():
            if key.lower() == model_id.lower():
                return value

        return None

    except Exception as e:
        logger.error(f"Error getting pricing for {gateway}/{model_id}: {e}")
        return None


def _is_building_catalog() -> bool:
    """Check if we're currently building the model catalog to avoid circular imports"""
    try:
        from src.services.models import _is_building_catalog as check_building
        return check_building()
    except ImportError:
        return False


def _get_cross_reference_pricing(model_id: str) -> dict[str, str] | None:
    """
    Get pricing for a gateway provider model by cross-referencing OpenRouter's catalog.

    Gateway providers (AiHubMix, Helicone, Anannas, Vercel) route to underlying providers
    like OpenAI, Anthropic, Google etc. This function extracts the underlying model ID
    and looks up its pricing from OpenRouter's cached models.

    Args:
        model_id: Model ID from gateway provider (e.g., "openai/gpt-4o", "gpt-4o-mini")

    Returns:
        Pricing dictionary or None if not found
    """
    # Avoid circular dependency during catalog building
    if _is_building_catalog():
        return None

    try:
        from src.services.models import get_cached_models

        # Get OpenRouter models from cache
        openrouter_models = get_cached_models("openrouter")
        if not openrouter_models:
            return None

        # Extract the base model name from the gateway model ID
        # e.g., "openai/gpt-4o" -> "gpt-4o", "anthropic/claude-3-opus" -> "claude-3-opus"
        base_model_id = model_id
        if "/" in model_id:
            parts = model_id.split("/")
            # Could be "provider/model" or "org/model-name"
            base_model_id = parts[-1]

        # Search for matching model in OpenRouter catalog
        for or_model in openrouter_models:
            if not isinstance(or_model, dict):
                continue

            or_id = or_model.get("id", "")
            or_pricing = or_model.get("pricing")

            if not or_pricing:
                continue

            # Check for exact match or suffix match
            # OpenRouter IDs are like "openai/gpt-4o", "anthropic/claude-3-opus-20240229"
            if or_id.endswith(f"/{base_model_id}") or or_id.endswith(f"/{model_id}"):
                # Return normalized pricing
                return {
                    "prompt": str(or_pricing.get("prompt", "0")),
                    "completion": str(or_pricing.get("completion", "0")),
                    "request": str(or_pricing.get("request", "0")),
                    "image": str(or_pricing.get("image", "0")),
                }

            # Also check if the base model ID matches the end of OpenRouter ID
            or_base = or_id.split("/")[-1] if "/" in or_id else or_id
            if or_base == base_model_id:
                return {
                    "prompt": str(or_pricing.get("prompt", "0")),
                    "completion": str(or_pricing.get("completion", "0")),
                    "request": str(or_pricing.get("request", "0")),
                    "image": str(or_pricing.get("image", "0")),
                }

        return None

    except Exception as e:
        logger.debug(f"Error getting cross-reference pricing for {model_id}: {e}")
        return None


def enrich_model_with_pricing(model_data: dict[str, Any], gateway: str) -> dict[str, Any] | None:
    """
    Enrich model data with manual pricing if available

    Args:
        model_data: Model dictionary
        gateway: Gateway name

    Returns:
        Enhanced model dictionary with pricing, or None if no pricing found for gateway providers
    """
    try:
        model_id = model_data.get("id")
        if not model_id:
            return model_data

        gateway_lower = gateway.lower()
        is_gateway_provider = gateway_lower in GATEWAY_PROVIDERS

        # Skip if pricing already exists and has non-zero values
        # (Zero pricing means no real pricing was set, so we should try to enrich)
        existing_pricing = model_data.get("pricing")
        if existing_pricing:
            # Check if any pricing value is non-zero using numeric comparison
            # This handles edge cases like scientific notation (1e-6) and various string formats
            def is_non_zero(v) -> bool:
                if v is None or v == "":
                    return False
                try:
                    return float(v) != 0.0
                except (ValueError, TypeError):
                    return False

            has_real_pricing = any(is_non_zero(v) for v in existing_pricing.values())
            if has_real_pricing:
                return model_data

        # Try to get manual pricing first
        manual_pricing = get_model_pricing(gateway, model_id)
        if manual_pricing:
            model_data["pricing"] = manual_pricing
            model_data["pricing_source"] = "manual"
            logger.debug(f"Enriched {model_id} with manual pricing from {gateway}")
            return model_data

        # For gateway providers, try cross-reference with OpenRouter
        if is_gateway_provider:
            cross_ref_pricing = _get_cross_reference_pricing(model_id)
            if cross_ref_pricing:
                model_data["pricing"] = cross_ref_pricing
                model_data["pricing_source"] = "cross-reference"
                logger.debug(f"Enriched {model_id} with cross-reference pricing from OpenRouter")
                return model_data

            # No pricing found for gateway provider - filter out this model
            logger.debug(f"No pricing found for gateway provider model {model_id}, filtering out")
            return None

        return model_data

    except Exception as e:
        logger.error(f"Error enriching model with pricing: {e}")
        return model_data


def get_all_gateway_pricing(gateway: str) -> dict[str, dict[str, str]]:
    """
    Get all pricing for a specific gateway

    Args:
        gateway: Gateway name

    Returns:
        Dictionary of model_id -> pricing
    """
    try:
        pricing_data = load_manual_pricing()

        if not pricing_data:
            return {}

        gateway_lower = gateway.lower()

        if gateway_lower not in pricing_data:
            return {}

        return pricing_data[gateway_lower]

    except Exception as e:
        logger.error(f"Error getting all pricing for {gateway}: {e}")
        return {}


def get_pricing_metadata() -> dict[str, Any]:
    """Get pricing metadata (last updated, sources, etc.)"""
    try:
        pricing_data = load_manual_pricing()
        return pricing_data.get("_metadata", {})
    except Exception as e:
        logger.error(f"Error getting pricing metadata: {e}")
        return {}


def refresh_pricing_cache():
    """Refresh the pricing cache by reloading from file"""
    global _pricing_cache
    _pricing_cache = None
    return load_manual_pricing()
