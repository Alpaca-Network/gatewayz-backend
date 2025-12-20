"""
Gateway Aggregator Service
Provides helper functions to aggregate data from multiple gateways
Reduces code duplication in catalog.py
"""

import logging
from typing import Any

from src.config.gateway_registry import (
    GATEWAY_REGISTRY,
    get_gateway_config,
    get_gateway_display_name,
)
from src.services.models import get_cached_models
from src.services.providers import get_cached_providers

logger = logging.getLogger(__name__)


class GatewayModels:
    """Container for models fetched from multiple gateways"""

    def __init__(self):
        self.models_by_gateway: dict[str, list[dict]] = {}

    def add_gateway_models(self, gateway_slug: str, models: list[dict]) -> None:
        """Add models for a specific gateway"""
        self.models_by_gateway[gateway_slug] = models

    def get_gateway_models(self, gateway_slug: str) -> list[dict]:
        """Get models for a specific gateway"""
        return self.models_by_gateway.get(gateway_slug, [])

    def get_all_models(self) -> list[dict]:
        """Get all models from all gateways combined"""
        all_models = []
        for models in self.models_by_gateway.values():
            all_models.extend(models)
        return all_models

    def get_models_for_gateway_value(self, gateway_value: str) -> list[dict]:
        """
        Get models based on gateway_value parameter

        If gateway_value is specific gateway, return only that gateway's models
        If gateway_value is "all", return combined models from all gateways
        """
        if gateway_value == "all":
            return self.get_all_models()
        return self.get_gateway_models(gateway_value)


def fetch_models_from_gateways(gateway_value: str) -> GatewayModels:
    """
    Fetch models from all matching gateways based on gateway_value parameter

    Args:
        gateway_value: Either a specific gateway slug or "all"

    Returns:
        GatewayModels object containing all fetched models
    """
    result = GatewayModels()

    for gateway_slug, config in GATEWAY_REGISTRY.items():
        # Check if we should fetch from this gateway
        if gateway_value not in (gateway_slug, "all"):
            continue

        # Fetch models from the gateway
        models = get_cached_models(gateway_slug) or []
        result.add_gateway_models(gateway_slug, models)

        # Log warning if models unavailable for specific gateway request
        if not models and gateway_value == gateway_slug:
            if config["supports_public_catalog"]:
                logger.warning(
                    f"{config['name']} models unavailable - continuing without them"
                )
            else:
                logger.info(
                    f"{config['name']} gateway requested but no cached catalog is available; "
                    f"returning an empty list because {config['name']} does not publish a public model listing"
                )

    return result


def fetch_providers_from_gateways(gateway_value: str) -> dict[str, list[dict]]:
    """
    Fetch providers from all matching gateways

    Args:
        gateway_value: Either a specific gateway slug or "all"

    Returns:
        Dictionary mapping gateway slugs to their provider lists
    """
    providers_by_gateway = {}

    # Handle OpenRouter separately since it has a dedicated providers endpoint
    if gateway_value in ("openrouter", "all"):
        raw_providers = get_cached_providers()
        if not raw_providers and gateway_value == "openrouter":
            logger.warning("OpenRouter provider data unavailable - returning empty response")
        providers_by_gateway["openrouter"] = raw_providers or []

    # For other gateways, we'll need to derive providers from models
    # This is handled separately in the catalog endpoints

    return providers_by_gateway


def get_gateway_models_mapping(gateway_value: str) -> dict[str, list[dict]]:
    """
    Get a simple dictionary mapping gateway slugs to model lists

    Args:
        gateway_value: Either a specific gateway slug or "all"

    Returns:
        Dictionary mapping gateway slugs to their model lists
    """
    gateway_models = fetch_models_from_gateways(gateway_value)
    return gateway_models.models_by_gateway


def filter_models_by_provider(
    models: list[dict],
    provider_filter: str | None
) -> list[dict]:
    """
    Filter models by provider slug

    Args:
        models: List of model dictionaries
        provider_filter: Provider slug to filter by (or None for no filter)

    Returns:
        Filtered list of models
    """
    if not provider_filter:
        return models

    filtered = []
    for model in models:
        # Try different fields to get provider slug
        provider_slug = (
            model.get("provider_slug") or
            model.get("provider") or
            (model.get("id", "").split("/")[0] if "/" in model.get("id", "") else None)
        )

        if provider_slug and provider_slug.lstrip("@").lower() == provider_filter.lower():
            filtered.append(model)

    return filtered


def apply_pagination(
    items: list[Any],
    limit: int | None = None,
    offset: int | None = None
) -> tuple[list[Any], dict[str, Any]]:
    """
    Apply pagination to a list of items

    Args:
        items: List of items to paginate
        limit: Maximum number of items to return
        offset: Number of items to skip

    Returns:
        Tuple of (paginated_items, pagination_metadata)
    """
    total = len(items)
    offset = offset or 0

    # Apply offset
    items = items[offset:] if offset else items

    # Apply limit
    items = items[:limit] if limit else items

    metadata = {
        "total": total,
        "returned": len(items),
        "offset": offset,
        "limit": limit,
    }

    return items, metadata


def derive_providers_from_gateway_models(
    gateway_models: GatewayModels,
    gateway_value: str
) -> list[dict]:
    """
    Derive provider list from models for each gateway

    Args:
        gateway_models: GatewayModels object containing models from all gateways
        gateway_value: Either a specific gateway slug or "all"

    Returns:
        List of provider dictionaries derived from models
    """
    providers_map: dict[str, dict] = {}

    for gateway_slug, models in gateway_models.models_by_gateway.items():
        # Check if we should process this gateway
        if gateway_value not in (gateway_slug, "all"):
            continue

        for model in models or []:
            # Try different fields to get provider name
            provider_slug = None

            # Try provider_slug field
            provider_slug = model.get("provider_slug") or model.get("provider")

            # Try extracting from model ID (format: provider/model-name)
            if not provider_slug:
                model_id = model.get("id", "")
                if "/" in model_id:
                    provider_slug = model_id.split("/")[0]

            # Try name field
            if not provider_slug:
                name = model.get("name", "")
                if "/" in name:
                    provider_slug = name.split("/")[0]

            if not provider_slug:
                continue

            # Clean up slug
            provider_slug = provider_slug.lstrip("@").lower()

            if provider_slug not in providers_map:
                providers_map[provider_slug] = {
                    "slug": provider_slug,
                    "site_url": model.get("provider_site_url"),
                    "logo_url": model.get("model_logo_url") or model.get("logo_url"),
                    "moderated_by_openrouter": False,
                    "source_gateway": gateway_slug,
                    "source_gateways": [gateway_slug],
                }
            else:
                # Merge source gateways
                existing = providers_map[provider_slug]
                if gateway_slug not in existing["source_gateways"]:
                    existing["source_gateways"].append(gateway_slug)

    return list(providers_map.values())


def merge_models_by_slug(*model_lists: list[dict]) -> list[dict]:
    """
    Merge multiple model lists by slug, avoiding duplicates

    Args:
        *model_lists: Variable number of model lists to merge

    Returns:
        Merged list of unique models
    """
    merged = []
    seen = set()

    for model_list in model_lists:
        for model in model_list or []:
            key = (model.get("canonical_slug") or model.get("id") or "").lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(model)

    return merged
