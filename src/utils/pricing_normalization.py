"""
Pricing Normalization Utilities

Standardizes pricing from various provider formats to per-token format.

All pricing in the system is standardized to cost per single token (e.g., 0.000000055).
This module handles conversion from different provider formats:
- Per-1M tokens (most common): OpenRouter, DeepInfra, etc.
- Per-1K tokens: some provider APIs
- Per-token: Already normalized

Created: 2026-01-19
Part of pricing standardization fix
"""

import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


class PricingFormat:
    """Enum for pricing formats from different providers"""

    PER_TOKEN = "per_token"
    PER_1K_TOKENS = "per_1k"
    PER_1M_TOKENS = "per_1m"


def normalize_to_per_token(
    price: float | str | Decimal | None, source_format: str = PricingFormat.PER_1M_TOKENS
) -> Decimal | None:
    """
    Normalize pricing from any format to per-token format.

    Args:
        price: Price value from provider API
        source_format: Format of the source price (default: per_1m)

    Returns:
        Price per single token as Decimal, or None if invalid

    Examples:
        >>> normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        Decimal('0.000000055')

        >>> normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        Decimal('0.000055')

        >>> normalize_to_per_token(0.000000055, PricingFormat.PER_TOKEN)
        Decimal('0.000000055')

        >>> normalize_to_per_token(-1, PricingFormat.PER_1M_TOKENS)
        None  # Dynamic pricing
    """
    if price is None or price == "":
        return None

    try:
        price_decimal = Decimal(str(price))

        # Handle negative values (dynamic pricing from OpenRouter)
        if price_decimal < 0:
            logger.debug(f"Skipping negative/dynamic pricing: {price}")
            return None

        # Handle zero
        if price_decimal == 0:
            return Decimal("0")

        # Normalize based on source format
        if source_format == PricingFormat.PER_TOKEN:
            # Already per-token
            return price_decimal
        elif source_format == PricingFormat.PER_1K_TOKENS:
            # Divide by 1,000 to get per-token
            return price_decimal / Decimal("1000")
        elif source_format == PricingFormat.PER_1M_TOKENS:
            # Divide by 1,000,000 to get per-token
            return price_decimal / Decimal("1000000")
        else:
            logger.error(f"Unknown pricing format: {source_format}")
            return None

    except (ValueError, TypeError, InvalidOperation) as e:
        logger.error(f"Failed to normalize price {price}: {e}")
        return None


def normalize_pricing_dict(pricing: dict, source_format: str = PricingFormat.PER_1M_TOKENS) -> dict:
    """
    Normalize all pricing fields in a dictionary.

    Args:
        pricing: Dict with 'prompt', 'completion', 'image', 'request' keys
        source_format: Format of source prices (default: per_1m)

    Returns:
        Dict with normalized per-token prices as strings

    Examples:
        >>> pricing = {"prompt": "0.055", "completion": "0.040"}
        >>> normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)
        {'prompt': '0.000000055', 'completion': '0.000000040', 'image': '0', 'request': '0'}
    """
    if not isinstance(pricing, dict):
        pricing = {}

    return {
        "prompt": str(normalize_to_per_token(pricing.get("prompt", 0), source_format) or "0"),
        "completion": str(
            normalize_to_per_token(pricing.get("completion", 0), source_format) or "0"
        ),
        "image": str(normalize_to_per_token(pricing.get("image", 0), source_format) or "0"),
        "request": str(normalize_to_per_token(pricing.get("request", 0), source_format) or "0"),
    }


def get_provider_format(provider_slug: str) -> str:
    """
    Get the pricing format used by a specific provider.

    Reads from the providers table metadata.pricing_format field.
    Falls back to PER_1M_TOKENS (most common format) if not set.

    Args:
        provider_slug: Provider identifier (e.g., 'openrouter', 'deepinfra')

    Returns:
        PricingFormat constant
    """
    try:
        from src.services.gateway_registry import get_gateway_registry

        registry = get_gateway_registry()
        entry = registry.get(provider_slug.lower())
        if entry and entry.get("pricing_format"):
            fmt = entry["pricing_format"]
            if fmt == "per_token":
                return PricingFormat.PER_TOKEN
            elif fmt == "per_1k":
                return PricingFormat.PER_1K_TOKENS
            elif fmt == "per_1m":
                return PricingFormat.PER_1M_TOKENS
    except Exception as exc:
        logger.warning("Failed to load pricing format from DB for %s: %s", provider_slug, exc)
    return PricingFormat.PER_1M_TOKENS


def auto_detect_format(price: float | str | Decimal) -> str:
    """
    Auto-detect pricing format based on value magnitude.

    This is a heuristic for database migration - detects what format
    existing prices are likely stored in.

    Args:
        price: Price value to analyze

    Returns:
        Detected PricingFormat

    Detection logic:
        - < 0.000001: Likely per-token
        - 0.000001 to 0.001: Likely per-1K
        - > 0.001: Likely per-1M

    Examples:
        >>> auto_detect_format(0.000000055)
        'per_token'
        >>> auto_detect_format(0.000055)
        'per_1k'
        >>> auto_detect_format(0.055)
        'per_1m'
    """
    try:
        price_float = float(price)

        if price_float < 0.000001:
            return PricingFormat.PER_TOKEN
        elif price_float < 0.001:
            return PricingFormat.PER_1K_TOKENS
        else:
            return PricingFormat.PER_1M_TOKENS
    except (ValueError, TypeError):
        # Default to per-1M if can't determine
        return PricingFormat.PER_1M_TOKENS


def convert_between_formats(
    price: float | str | Decimal, from_format: str, to_format: str
) -> Decimal | None:
    """
    Convert price from one format to another.

    Args:
        price: Price value to convert
        from_format: Source format
        to_format: Target format

    Returns:
        Converted price as Decimal

    Examples:
        >>> convert_between_formats(0.055, PricingFormat.PER_1M_TOKENS, PricingFormat.PER_TOKEN)
        Decimal('0.000000055')

        >>> convert_between_formats(0.055, PricingFormat.PER_1K_TOKENS, PricingFormat.PER_1M_TOKENS)
        Decimal('55')
    """
    # First normalize to per-token
    per_token = normalize_to_per_token(price, from_format)

    if per_token is None:
        return None

    # Then convert to target format
    if to_format == PricingFormat.PER_TOKEN:
        return per_token
    elif to_format == PricingFormat.PER_1K_TOKENS:
        return per_token * Decimal("1000")
    elif to_format == PricingFormat.PER_1M_TOKENS:
        return per_token * Decimal("1000000")
    else:
        return None


def validate_normalized_price(price: Decimal | float | str) -> bool:
    """
    Validate that a price is in correct per-token format.

    Per-token prices should be very small (< 0.001).

    Args:
        price: Price to validate

    Returns:
        True if price appears to be in per-token format

    Examples:
        >>> validate_normalized_price(0.000000055)
        True
        >>> validate_normalized_price(0.055)
        False  # Too large, likely per-1M
    """
    try:
        price_float = float(price)

        # Per-token prices should be < 0.001
        # (Even expensive models like GPT-4 are ~$0.00003 per token)
        if price_float < 0.001:
            return True
        else:
            logger.warning(
                f"Price {price_float} is suspiciously high for per-token format "
                f"(expected < 0.001)"
            )
            return False
    except (ValueError, TypeError):
        return False


# Convenience function for backward compatibility
def normalize_price_from_provider(
    price: float | str | Decimal | None, provider_slug: str
) -> Decimal | None:
    """
    Normalize price from a specific provider to per-token format.

    This is a convenience wrapper that auto-detects the provider's format.

    Args:
        price: Price from provider API
        provider_slug: Provider identifier

    Returns:
        Normalized per-token price

    Examples:
        >>> normalize_price_from_provider(0.055, "deepinfra")
        Decimal('0.000000055')  # DeepInfra uses per-1M

        >>> normalize_price_from_provider(0.055, "clarifai")
        Decimal('0.000055')  # per-1K provider
    """
    provider_format = get_provider_format(provider_slug)
    return normalize_to_per_token(price, provider_format)
