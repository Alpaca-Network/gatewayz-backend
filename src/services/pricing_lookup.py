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
# Models without valid pricing will be filtered out to avoid appearing as "free"
GATEWAY_PROVIDERS = {
    "aihubmix",
    "akash",
    "alibaba-cloud",
    "anannas",
    "anthropic",  # Direct Anthropic API - needs cross-reference for model ID matching
    "clarifai",
    "cloudflare-workers-ai",
    "deepinfra",
    "featherless",
    "fireworks",
    "groq",
    "helicone",
    "onerouter",
    "together",
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
        Pricing dictionary (normalized to per-token format) or None if not found
    """
    try:
        from src.services.pricing_normalization import normalize_pricing_dict, get_provider_format

        pricing_data = load_manual_pricing()

        if not pricing_data:
            return None

        gateway_lower = gateway.lower()

        if gateway_lower not in pricing_data:
            return None

        gateway_pricing = pricing_data[gateway_lower]

        raw_pricing = None
        if model_id in gateway_pricing:
            raw_pricing = gateway_pricing[model_id]
        else:
            # Try case-insensitive match
            for key, value in gateway_pricing.items():
                if key.lower() == model_id.lower():
                    raw_pricing = value
                    break

        if raw_pricing is None:
            return None

        # Normalize pricing based on provider format
        # Default to per-1M (most common format in manual_pricing.json)
        provider_format = get_provider_format(gateway_lower)
        normalized = normalize_pricing_dict(raw_pricing, provider_format)

        return normalized

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
        Pricing dictionary (normalized to per-token format) or None if not found
    """
    # Avoid circular dependency during catalog building
    if _is_building_catalog():
        return None

    try:
        from src.services.models import get_cached_models
        from src.services.pricing_normalization import normalize_pricing_dict, PricingFormat

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
                # Normalize OpenRouter pricing (which is per-1M tokens) to per-token
                return normalize_pricing_dict(or_pricing, PricingFormat.PER_1M_TOKENS)

            # Also check if the base model ID matches the end of OpenRouter ID
            or_base = or_id.split("/")[-1] if "/" in or_id else or_id
            if or_base == base_model_id:
                # Normalize OpenRouter pricing (which is per-1M tokens) to per-token
                return normalize_pricing_dict(or_pricing, PricingFormat.PER_1M_TOKENS)

            # Handle versioned model IDs (e.g., "claude-3-opus" matching "claude-3-opus-20240229")
            # OpenRouter often uses date-versioned IDs like "anthropic/claude-3-opus-20240229"
            # Note: We need to check that the suffix is a date version, not a different model variant
            # e.g., "gpt-4o" should NOT match "gpt-4o-mini" but SHOULD match "gpt-4o-20240513"
            if or_base.startswith(base_model_id):
                suffix = or_base[len(base_model_id):]
                # Only match if suffix is empty or looks like a date version (starts with '-' followed by digits)
                if not suffix or (suffix.startswith("-") and len(suffix) > 1 and suffix[1:].replace("-", "").isdigit()):
                    return normalize_pricing_dict(or_pricing, PricingFormat.PER_1M_TOKENS)
            # Also check reverse: base_model_id starts with or_base (for versioned queries)
            if base_model_id.startswith(or_base):
                suffix = base_model_id[len(or_base):]
                if not suffix or (suffix.startswith("-") and len(suffix) > 1 and suffix[1:].replace("-", "").isdigit()):
                    return normalize_pricing_dict(or_pricing, PricingFormat.PER_1M_TOKENS)

        return None

    except Exception as e:
        logger.debug(f"Error getting cross-reference pricing for {model_id}: {e}")
        return None


def _get_pricing_from_database(model_id: str) -> dict[str, str] | None:
    """
    Get pricing from database (Phase 2: database-first approach).

    Args:
        model_id: Model identifier (e.g., "nosana/meta-llama/Llama-3.3-70B-Instruct")

    Returns:
        Pricing dictionary in per-token format (consistent with all other sources):
        {
            "prompt": "0.0000009",  # per-token
            "completion": "0.0000009",  # per-token
            "request": "0",
            "image": "0"
        }
        or None if not found
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Query models table with JOIN to model_pricing table
        # Note: model_id column was removed - now use model_name as canonical identifier
        result = (
            client.table("models")
            .select("id, model_name, model_pricing(price_per_input_token, price_per_output_token)")
            .eq("model_name", model_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if not result.data or not result.data[0]:
            return None

        row = result.data[0]

        if not row.get("model_pricing"):
            return None

        pricing_data = row["model_pricing"]
        if isinstance(pricing_data, list):
            if not pricing_data:
                return None
            pricing_data = pricing_data[0]

        prompt_price = pricing_data.get("price_per_input_token")
        completion_price = pricing_data.get("price_per_output_token")

        if prompt_price is None or completion_price is None:
            return None

        # Return per-token format (consistent with manual and cross-reference sources)
        # Database stores per-token (e.g., 0.0000009)
        # Frontend handles conversion to per-million for display
        return {
            "prompt": str(prompt_price),
            "completion": str(completion_price),
            "request": "0",
            "image": "0"
        }

    except Exception as e:
        logger.error(f"Database pricing lookup failed for {model_id}: {e}")
        return None


def enrich_model_with_pricing(model_data: dict[str, Any], gateway: str) -> dict[str, Any] | None:
    """
    Enrich model data with pricing information.

    Phase 2 Update: Database-first approach with JSON fallback.

    Lookup priority:
    1. Database (model_pricing table) ← NEW
    2. Manual pricing JSON (fallback)
    3. Cross-reference (for gateway providers)

    Args:
        model_data: Model dictionary
        gateway: Gateway name

    Returns:
        Enhanced model dictionary with pricing, or None if no pricing found for gateway providers
    """
    model_id = model_data.get("id")
    if not model_id:
        return model_data

    gateway_lower = gateway.lower()
    is_gateway_provider = gateway_lower in GATEWAY_PROVIDERS

    # Only OpenRouter has legitimately free models (those with :free suffix)
    # All other providers/gateways should not be marked as free
    if gateway_lower != "openrouter":
        model_data["is_free"] = False

    # Helper function to check if a pricing value is non-zero
    # This handles edge cases like scientific notation (1e-6) and various string formats
    def is_non_zero(v) -> bool:
        if v is None or v == "":
            return False
        try:
            return float(v) != 0.0
        except (ValueError, TypeError):
            return False

    try:
        # Skip if pricing already exists and has non-zero values
        # (Zero pricing means no real pricing was set, so we should try to enrich)
        existing_pricing = model_data.get("pricing")
        if existing_pricing:
            # Check if any pricing value is non-zero using numeric comparison
            has_real_pricing = any(is_non_zero(v) for v in existing_pricing.values())
            if has_real_pricing:
                return model_data

        # PHASE 2: Try database first (NEW)
        db_pricing = _get_pricing_from_database(model_id)
        if db_pricing:
            model_data["pricing"] = db_pricing
            model_data["pricing_source"] = "database"
            logger.debug(f"[Phase 2] Enriched {model_id} with database pricing")
            return model_data

        # Fallback to manual pricing JSON
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
                # Verify cross-reference pricing has non-zero values
                # Models with zero pricing from OpenRouter should still be filtered out
                has_valid_pricing = any(
                    is_non_zero(v) for k, v in cross_ref_pricing.items()
                    if k in ("prompt", "completion")
                )
                if has_valid_pricing:
                    model_data["pricing"] = cross_ref_pricing
                    model_data["pricing_source"] = "cross-reference"
                    logger.debug(f"Enriched {model_id} with cross-reference pricing from OpenRouter")
                    return model_data
                else:
                    logger.debug(f"Cross-reference pricing for {model_id} is zero, filtering out")

            # During catalog build, return the model with zero pricing instead of filtering
            # This prevents models from disappearing during initial build. They'll get
            # proper pricing during background refresh when cross-reference is available.
            if _is_building_catalog():
                logger.debug(f"Catalog building: keeping {model_id} with zero pricing")
                return model_data

            # No pricing found for gateway provider - filter out this model
            logger.debug(f"No pricing found for gateway provider model {model_id}, filtering out")
            return None

        return model_data

    except Exception as e:
        logger.error(f"Error enriching model with pricing: {e}")
        # For gateway providers, still filter out if we couldn't determine pricing
        # This prevents gateway models from appearing as free due to errors
        if is_gateway_provider:
            logger.debug(f"Filtering out gateway provider model {model_id} due to error")
            return None
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
