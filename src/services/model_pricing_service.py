"""
Model Pricing Service

Centralized service for fetching and managing model pricing.
All prices are stored in per-token format in the model_pricing table.
"""
import logging
from decimal import Decimal
from typing import Optional

from src.config.supabase_config import get_supabase_client
from src.services.pricing_normalization import (
    normalize_pricing_dict,
    get_provider_format,
    PricingFormat,
)

logger = logging.getLogger(__name__)

# Cache for pricing lookups
_pricing_cache: dict[int, dict] = {}


def get_model_pricing_by_id(model_id: int) -> Optional[dict]:
    """
    Get normalized per-token pricing for a model by ID

    Args:
        model_id: Model ID

    Returns:
        Dict with pricing or None if not found
        {
            "price_per_input_token": 0.000000055,
            "price_per_output_token": 0.000000075,
            "price_per_image_token": None,
            "price_per_request": None,
            "pricing_source": "provider"
        }
    """
    # Check cache first
    if model_id in _pricing_cache:
        return _pricing_cache[model_id]

    try:
        supabase = get_supabase_client()
        response = supabase.table("model_pricing").select("*").eq("model_id", model_id).execute()

        if response.data:
            pricing = response.data[0]
            _pricing_cache[model_id] = pricing
            return pricing

        return None

    except Exception as e:
        logger.error(f"Error fetching pricing for model {model_id}: {e}")
        return None


def get_model_pricing_by_name(model_name: str, provider: str) -> Optional[dict]:
    """
    Get normalized per-token pricing for a model by name and provider

    Args:
        model_name: Model name/ID
        provider: Provider/gateway name

    Returns:
        Dict with pricing or None if not found
    """
    try:
        supabase = get_supabase_client()

        # First get the model ID
        response = (
            supabase.table("models")
            .select("id")
            .eq("id", model_name)
            .eq("source_gateway", provider)
            .execute()
        )

        if not response.data:
            return None

        model_id = response.data[0]["id"]
        return get_model_pricing_by_id(model_id)

    except Exception as e:
        logger.error(f"Error fetching pricing for {provider}/{model_name}: {e}")
        return None


def calculate_cost(
    model_id: int,
    input_tokens: int,
    output_tokens: int,
    image_tokens: Optional[int] = None,
) -> dict:
    """
    Calculate cost for a request using normalized per-token pricing

    Args:
        model_id: Model ID
        input_tokens: Number of input/prompt tokens
        output_tokens: Number of output/completion tokens
        image_tokens: Number of image tokens (optional)

    Returns:
        Dict with cost breakdown:
        {
            "input_cost": 0.000055,
            "output_cost": 0.000075,
            "image_cost": 0.0,
            "total_cost": 0.000130,
            "currency": "USD"
        }
    """
    pricing = get_model_pricing_by_id(model_id)

    if not pricing:
        logger.warning(f"No pricing found for model {model_id}, returning zero cost")
        return {
            "input_cost": 0.0,
            "output_cost": 0.0,
            "image_cost": 0.0,
            "total_cost": 0.0,
            "currency": "USD",
        }

    # All prices are per-token, so just multiply
    input_cost = input_tokens * float(pricing.get("price_per_input_token", 0))
    output_cost = output_tokens * float(pricing.get("price_per_output_token", 0))
    image_cost = 0.0

    if image_tokens and pricing.get("price_per_image_token"):
        image_cost = image_tokens * float(pricing["price_per_image_token"])

    # Check for per-request pricing
    if pricing.get("price_per_request"):
        # Add per-request cost on top
        total_cost = input_cost + output_cost + image_cost + float(pricing["price_per_request"])
    else:
        total_cost = input_cost + output_cost + image_cost

    return {
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "image_cost": round(image_cost, 8),
        "total_cost": round(total_cost, 8),
        "currency": "USD",
    }


def upsert_model_pricing(
    model_id: int,
    price_per_input_token: float,
    price_per_output_token: float,
    price_per_image_token: Optional[float] = None,
    price_per_request: Optional[float] = None,
    pricing_source: str = "provider",
) -> bool:
    """
    Insert or update pricing for a model

    Args:
        model_id: Model ID
        price_per_input_token: Price per input token (must be per-token format)
        price_per_output_token: Price per output token (must be per-token format)
        price_per_image_token: Price per image token (optional)
        price_per_request: Price per request (optional)
        pricing_source: Source of pricing (provider, manual, etc.)

    Returns:
        True if successful, False otherwise
    """
    try:
        supabase = get_supabase_client()

        pricing_data = {
            "model_id": model_id,
            "price_per_input_token": price_per_input_token,
            "price_per_output_token": price_per_output_token,
            "price_per_image_token": price_per_image_token,
            "price_per_request": price_per_request,
            "pricing_source": pricing_source,
        }

        # Upsert (insert or update)
        supabase.table("model_pricing").upsert(pricing_data).execute()

        # Clear cache for this model
        if model_id in _pricing_cache:
            del _pricing_cache[model_id]

        logger.info(f"Updated pricing for model {model_id}")
        return True

    except Exception as e:
        logger.error(f"Error upserting pricing for model {model_id}: {e}")
        return False


def bulk_upsert_pricing(pricing_records: list[dict]) -> tuple[int, int]:
    """
    Bulk insert/update pricing records

    Args:
        pricing_records: List of pricing dicts (must include model_id and prices)

    Returns:
        Tuple of (success_count, error_count)
    """
    try:
        supabase = get_supabase_client()

        # Deduplicate by model_id - keep the last occurrence
        # This prevents unique constraint violations when the same model_id appears multiple times
        unique_records = {}
        for record in pricing_records:
            model_id = record.get("model_id")
            if model_id is not None:
                unique_records[model_id] = record

        deduplicated_records = list(unique_records.values())

        if len(deduplicated_records) < len(pricing_records):
            duplicates_removed = len(pricing_records) - len(deduplicated_records)
            logger.warning(
                f"Removed {duplicates_removed} duplicate model_id entries from pricing records "
                f"(original: {len(pricing_records)}, deduplicated: {len(deduplicated_records)})"
            )

        # Batch upsert
        supabase.table("model_pricing").upsert(deduplicated_records).execute()

        # Clear cache
        _pricing_cache.clear()

        logger.info(f"Bulk upserted {len(deduplicated_records)} pricing records")
        return (len(deduplicated_records), 0)

    except Exception as e:
        logger.error(f"Error bulk upserting pricing: {e}")
        return (0, len(pricing_records))


def clear_pricing_cache():
    """Clear the pricing cache"""
    _pricing_cache.clear()
    logger.info("Pricing cache cleared")


def get_pricing_stats() -> dict:
    """
    Get statistics about pricing data

    Returns:
        Dict with stats:
        {
            "total_models_with_pricing": 1000,
            "avg_input_price": 0.000000050,
            "avg_output_price": 0.000000075,
            "min_input_price": 0.000000001,
            "max_input_price": 0.000000200
        }
    """
    try:
        supabase = get_supabase_client()
        response = supabase.table("model_pricing").select("*").execute()

        if not response.data:
            return {}

        prices = response.data

        input_prices = [p["price_per_input_token"] for p in prices if p.get("price_per_input_token")]
        output_prices = [p["price_per_output_token"] for p in prices if p.get("price_per_output_token")]

        return {
            "total_models_with_pricing": len(prices),
            "avg_input_price": sum(input_prices) / len(input_prices) if input_prices else 0,
            "avg_output_price": sum(output_prices) / len(output_prices) if output_prices else 0,
            "min_input_price": min(input_prices) if input_prices else 0,
            "max_input_price": max(input_prices) if input_prices else 0,
            "min_output_price": min(output_prices) if output_prices else 0,
            "max_output_price": max(output_prices) if output_prices else 0,
        }

    except Exception as e:
        logger.error(f"Error getting pricing stats: {e}")
        return {}
