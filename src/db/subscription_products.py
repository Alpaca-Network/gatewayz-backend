#!/usr/bin/env python3
"""
Subscription Products Database Module
Handles retrieval of subscription product configurations
"""

import json
import logging
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from postgrest import APIError

from src.config.supabase_config import get_supabase_client
from src.db.postgrest_schema import is_schema_cache_error, refresh_postgrest_schema_cache
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

_FALLBACK_ENV_VAR = "SUBSCRIPTION_PRODUCT_FALLBACKS"
_DEFAULT_SUBSCRIPTION_PRODUCT_FALLBACKS: tuple[dict[str, Any], ...] = (
    {
        "product_id": "prod_TKOqQPhVRxNp4Q",
        "tier": "pro",
        "display_name": "Pro",
        "credits_per_month": 20.0,
        "description": "Professional tier with $20 monthly credits",
        "is_active": True,
    },
    {
        "product_id": "prod_TKOqRE2L6qXu7s",
        "tier": "max",
        "display_name": "MAX",
        "credits_per_month": 150.0,
        "description": "Maximum tier with $150 monthly credits",
        "is_active": True,
    },
)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _normalize_fallback_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    product_id = entry.get("product_id")
    tier = entry.get("tier")
    credits_value = entry.get("credits_per_month")

    if not product_id or not tier:
        return None

    try:
        credits = float(credits_value)
    except (TypeError, ValueError):
        return None

    normalized = {
        "product_id": str(product_id),
        "tier": str(tier),
        "display_name": entry.get("display_name") or str(tier).title(),
        "credits_per_month": credits,
        "description": entry.get("description"),
        "is_active": _coerce_bool(entry.get("is_active", True)),
    }
    return normalized


def _load_env_fallback_entries() -> list[dict[str, Any]]:
    raw = os.getenv(_FALLBACK_ENV_VAR)
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Ignoring %s override due to JSON parse error: %s",
            _FALLBACK_ENV_VAR,
            sanitize_for_logging(str(exc)),
        )
        return []

    if isinstance(parsed, dict):
        parsed = [parsed]

    normalized_entries: list[dict[str, Any]] = []
    for candidate in parsed:
        normalized = _normalize_fallback_entry(candidate)
        if normalized:
            normalized_entries.append(normalized)
        else:
            logger.debug(
                "Skipped invalid subscription product fallback entry: %s",
                sanitize_for_logging(str(candidate)),
            )
    return normalized_entries


@lru_cache(maxsize=1)
def _fallback_entries() -> tuple[dict[str, Any], ...]:
    merged: dict[str, dict[str, Any]] = {
        entry["product_id"]: {**entry} for entry in _DEFAULT_SUBSCRIPTION_PRODUCT_FALLBACKS
    }

    for override in _load_env_fallback_entries():
        merged[override["product_id"]] = {**merged.get(override["product_id"], {}), **override}

    return tuple(merged.values())


@lru_cache(maxsize=1)
def _fallback_products_by_id() -> dict[str, dict[str, Any]]:
    return {entry["product_id"]: entry for entry in _fallback_entries()}


@lru_cache(maxsize=1)
def _fallback_products_by_tier() -> dict[str, list[dict[str, Any]]]:
    tier_map: dict[str, list[dict[str, Any]]] = {}
    for entry in _fallback_entries():
        tier_key = entry["tier"].lower()
        tier_map.setdefault(tier_key, []).append(entry)
    return tier_map


def _get_fallback_product(product_id: str) -> dict[str, Any] | None:
    if not product_id:
        return None
    product = _fallback_products_by_id().get(product_id)
    return {**product} if product else None


def _get_fallback_product_by_tier(tier: str) -> dict[str, Any] | None:
    if not tier:
        return None
    products = _fallback_products_by_tier().get(tier.lower(), [])
    for product in products:
        if product.get("is_active", True):
            return {**product}
    return None


def _reset_fallback_cache() -> None:  # pragma: no cover - helper for tests
    _fallback_entries.cache_clear()
    _fallback_products_by_id.cache_clear()
    _fallback_products_by_tier.cache_clear()


def _execute_with_schema_cache_retry(operation: Callable[[], Any]) -> Any:
    """
    Execute a Supabase operation and transparently retry once on schema cache errors.
    """
    try:
        return operation()
    except APIError as api_error:
        if not is_schema_cache_error(api_error):
            raise

        logger.warning(
            "subscription_products query failed due to PostgREST schema cache miss (%s); attempting refresh",
            sanitize_for_logging(str(api_error)),
        )

        if not refresh_postgrest_schema_cache():
            raise

        try:
            return operation()
        except APIError as retry_error:
            logger.error(
                "subscription_products query failed again after schema cache refresh: %s",
                sanitize_for_logging(str(retry_error)),
                exc_info=True,
            )
            raise


def _fallback_tier_from_product(product_id: str, *, reason: str) -> str | None:
    fallback_product = _get_fallback_product(product_id)
    if fallback_product:
        tier = fallback_product["tier"]
        logger.info(
            "Product %s resolved via fallback mapping after %s; tier=%s",
            product_id,
            reason,
            tier,
        )
        return tier
    return None


def _fallback_credits_from_tier(tier: str, *, reason: str) -> float | None:
    fallback_product = _get_fallback_product_by_tier(tier)
    if fallback_product:
        credits = float(fallback_product["credits_per_month"])
        logger.info(
            "Tier %s resolved via fallback mapping after %s; credits_per_month=%s",
            tier,
            reason,
            credits,
        )
        return credits
    return None


def _fallback_product_snapshot(product_id: str, *, reason: str) -> dict[str, Any] | None:
    fallback_product = _get_fallback_product(product_id)
    if fallback_product and fallback_product.get("is_active", True):
        logger.info(
            "Product %s configuration served via fallback mapping after %s.",
            product_id,
            reason,
        )
        return fallback_product
    return None


def _fallback_active_product_list(*, reason: str) -> list[dict[str, Any]]:
    products = [
        {**product}
        for product in _fallback_entries()
        if product.get("is_active", True)
    ]
    if products:
        logger.info("Serving %s subscription products from fallback mapping (%s).", len(products), reason)
    return products


def get_tier_from_product_id(product_id: str) -> str:
    """
    Get subscription tier from Stripe product ID

    Args:
        product_id: Stripe product ID (e.g., prod_TKOqQPhVRxNp4Q)

    Returns:
        Tier name ('basic', 'pro', 'max', etc.) - defaults to 'basic' if not found
    """
    try:
        client = get_supabase_client()

        def query():
            return (
                client.table("subscription_products")
                .select("tier")
                .eq("product_id", product_id)
                .eq("is_active", True)
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            tier = result.data[0]["tier"]
            logger.info(f"Product {product_id} mapped to tier: {tier}")
            return tier

        fallback_tier = _fallback_tier_from_product(
            product_id, reason="empty Supabase result for subscription_products"
        )
        if fallback_tier:
            return fallback_tier

        logger.warning(f"Product {product_id} not found, defaulting to 'basic' tier")
        return "basic"

    except Exception as e:
        logger.error(f"Error getting tier from product ID: {e}", exc_info=True)
        fallback_tier = _fallback_tier_from_product(
            product_id, reason="Supabase error while reading subscription_products"
        )
        if fallback_tier:
            return fallback_tier

        # Default to basic tier on error
        return "basic"


def get_credits_from_tier(tier: str) -> float:
    """
    Get monthly credit allocation for a subscription tier

    Args:
        tier: Subscription tier ('pro', 'max', etc.)

    Returns:
        Monthly credits in USD - defaults to 0 if not found
    """
    try:
        client = get_supabase_client()

        def query():
            return (
                client.table("subscription_products")
                .select("credits_per_month")
                .eq("tier", tier)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            credits = float(result.data[0]["credits_per_month"])
            logger.info(f"Tier {tier} has {credits} monthly credits")
            return credits

        fallback_credits = _fallback_credits_from_tier(
            tier, reason="empty Supabase result for subscription_products"
        )
        if fallback_credits is not None:
            return fallback_credits

        logger.warning(f"Tier {tier} not found, defaulting to 0 credits")
        return 0.0

    except Exception as e:
        logger.error(f"Error getting credits from tier: {e}", exc_info=True)
        fallback_credits = _fallback_credits_from_tier(
            tier, reason="Supabase error while reading subscription_products"
        )
        if fallback_credits is not None:
            return fallback_credits

        # Default to 0 credits on error
        return 0.0


def get_subscription_product(product_id: str) -> dict[str, Any] | None:
    """
    Get full subscription product configuration

    Args:
        product_id: Stripe product ID

    Returns:
        Product configuration dict or None if not found
    """
    try:
        client = get_supabase_client()

        def query():
            return (
                client.table("subscription_products")
                .select("*")
                .eq("product_id", product_id)
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            return result.data[0]

        return _fallback_product_snapshot(
            product_id, reason="empty Supabase result for subscription_products"
        )

    except Exception as e:
        logger.error(f"Error getting subscription product: {e}", exc_info=True)
        return _fallback_product_snapshot(
            product_id, reason="Supabase error while reading subscription_products"
        )


def get_all_active_products() -> list[dict[str, Any]]:
    """
    Get all active subscription products

    Returns:
        List of active product configurations
    """
    try:
        client = get_supabase_client()

        def query():
            return (
                client.table("subscription_products")
                .select("*")
                .eq("is_active", True)
                .order("credits_per_month")
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            return result.data

        fallback_products = _fallback_active_product_list(
            reason="empty Supabase result for subscription_products"
        )
        return fallback_products

    except Exception as e:
        logger.error(f"Error getting active products: {e}", exc_info=True)
        fallback_products = _fallback_active_product_list(
            reason="Supabase error while reading subscription_products"
        )
        return fallback_products or []


def add_subscription_product(
    product_id: str,
    tier: str,
    display_name: str,
    credits_per_month: float,
    description: str | None = None,
    is_active: bool = True,
) -> bool:
    """
    Add a new subscription product configuration

    Args:
        product_id: Stripe product ID
        tier: Subscription tier
        display_name: Display-friendly name
        credits_per_month: Monthly credit allocation
        description: Product description
        is_active: Whether product is active

    Returns:
        True if added successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        def query():
            return (
                client.table("subscription_products")
                .insert(
                    {
                        "product_id": product_id,
                        "tier": tier,
                        "display_name": display_name,
                        "credits_per_month": credits_per_month,
                        "description": description,
                        "is_active": is_active,
                    }
                )
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            logger.info(f"Added subscription product: {product_id} ({tier})")
            return True
        else:
            logger.error(f"Failed to add subscription product: {product_id}")
            return False

    except Exception as e:
        logger.error(f"Error adding subscription product: {e}", exc_info=True)
        return False


def update_subscription_product(
    product_id: str,
    tier: str | None = None,
    display_name: str | None = None,
    credits_per_month: float | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> bool:
    """
    Update an existing subscription product configuration

    Args:
        product_id: Stripe product ID
        tier: New tier (optional)
        display_name: New display name (optional)
        credits_per_month: New credit allocation (optional)
        description: New description (optional)
        is_active: New active status (optional)

    Returns:
        True if updated successfully, False otherwise
    """
    try:
        client = get_supabase_client()

        # Build update dict with only provided fields
        update_data = {}
        if tier is not None:
            update_data["tier"] = tier
        if display_name is not None:
            update_data["display_name"] = display_name
        if credits_per_month is not None:
            update_data["credits_per_month"] = credits_per_month
        if description is not None:
            update_data["description"] = description
        if is_active is not None:
            update_data["is_active"] = is_active

        if not update_data:
            logger.warning("No fields to update for subscription product")
            return False

        def query():
            return (
                client.table("subscription_products")
                .update(update_data)
                .eq("product_id", product_id)
                .execute()
            )

        result = _execute_with_schema_cache_retry(query)

        if result.data:
            logger.info(f"Updated subscription product: {product_id}")
            return True
        else:
            logger.error(f"Failed to update subscription product: {product_id}")
            return False

    except Exception as e:
        logger.error(f"Error updating subscription product: {e}", exc_info=True)
        return False
