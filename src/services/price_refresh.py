"""
Lightweight, price-only background refresh.

This is a deliberately minimal alternative to the full scheduled sync
(``src.services.model_catalog_sync.sync_all_providers``). The full
sync fetches every model from every provider, writes many columns, runs stale
detection, and rebuilds/warms the entire catalog cache — which blocks resources
for 10-20 minutes and causes 499 errors.

This job ONLY keeps PRICES current so per-request billing never drifts below
provider cost. For every model that ALREADY EXISTS in the DB, it compares the
freshly-fetched per-token price to the stored price and, when they differ,
updates ONLY the pricing (``models.metadata.pricing_raw`` + the ``model_pricing``
table). It does NOT:
  - insert new models,
  - deprecate/deactivate/delete models,
  - touch non-pricing columns,
  - rebuild or warm the full catalog cache.

Price extraction and normalization reuse the EXACT same logic that the full sync
uses in ``model_catalog_sync.transform_normalized_model_to_db_schema`` so units
match (everything stored as cost per SINGLE token). A unit mismatch here would
1000x mis-price billing, so the normalization helpers are imported directly
rather than re-implemented.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Pricing fields, in the order/spelling used everywhere else in the codebase.
_PRICING_FIELDS = ("prompt", "completion", "image", "request")


def _extract_normalized_pricing(
    normalized_model: dict[str, Any], provider_slug: str
) -> dict[str, str]:
    """
    Extract per-token pricing from a fetched model, mirroring EXACTLY the pricing
    block of ``transform_normalized_model_to_db_schema`` (model_catalog_sync.py).

    Steps (identical to full sync):
      1. ``extract_pricing`` pulls prompt/completion/image/request as Decimals.
      2. Free models (``is_free``) are forced to zero pricing.
      3. If the provider's pricing format is not already per-token, each non-zero
         value is normalized to per-token via ``normalize_to_per_token`` using the
         provider's format (per-1M / per-1K / per-token).

    Returns a dict of stringified per-token prices for the four fields. Fields
    whose value is None (unknown) are omitted so we never overwrite a known price
    with a guessed zero.
    """
    # Import inside the function to keep this module import-light and to reuse the
    # canonical helpers (no re-implementation of price math).
    from src.services.model_catalog_sync import extract_pricing
    from src.utils.pricing_normalization import (
        PricingFormat,
        get_provider_format,
        normalize_to_per_token,
    )

    pricing = extract_pricing(normalized_model)

    # Free models (e.g. :free suffix) must have zero pricing — same as full sync.
    if normalized_model.get("is_free"):
        for field in _PRICING_FIELDS:
            pricing[field] = Decimal("0")

    # Normalize to per-token when the provider uses a different unit. Mirrors
    # the full-sync block exactly (same source_gateway fallback and guards).
    source_gateway = normalized_model.get("source_gateway", provider_slug)
    provider_format = get_provider_format(source_gateway)
    if provider_format != PricingFormat.PER_TOKEN:
        for field in _PRICING_FIELDS:
            if pricing[field] is not None and pricing[field] != Decimal("0"):
                normalized_val = normalize_to_per_token(pricing[field], provider_format)
                pricing[field] = normalized_val if normalized_val is not None else Decimal("0")

    # Stringify (matching how pricing_raw is stored) and drop unknown (None) fields.
    result: dict[str, str] = {}
    for field in _PRICING_FIELDS:
        value = pricing[field]
        if value is not None:
            result[field] = str(value)
    return result


def _pricing_differs(new_pricing: dict[str, str], stored_pricing: Any) -> bool:
    """
    True if the freshly-fetched pricing differs from what's stored.

    Compares the four pricing fields by Decimal value (so "0.0000010" and
    "0.000001" are treated as equal). A field missing on either side that is
    present-and-nonzero on the other counts as a difference.
    """
    if not new_pricing:
        # Nothing meaningful fetched — never trigger a write.
        return False

    stored = stored_pricing if isinstance(stored_pricing, dict) else {}

    for field in _PRICING_FIELDS:
        new_has = field in new_pricing
        old_has = field in stored and stored.get(field) is not None
        if not new_has and not old_has:
            continue

        def _to_decimal(v: Any) -> Decimal | None:
            if v is None:
                return None
            try:
                return Decimal(str(v))
            except Exception:
                return None

        new_val = _to_decimal(new_pricing.get(field)) if new_has else None
        old_val = _to_decimal(stored.get(field)) if old_has else None

        if new_val != old_val:
            return True

    return False


def _refresh_provider_prices(provider_slug: str, dry_run: bool) -> dict[str, Any]:
    """
    Refresh prices for a single provider.

    Fetches the provider's models, loads the current pricing rows for that
    provider from the DB, and for each fetched model that ALREADY exists,
    updates pricing only when it changed. New models are skipped (not inserted).

    Returns per-provider counters. Raises on hard failures so the caller can
    record the provider as failed (per-provider isolation lives in the caller).
    """
    from src.db.models_catalog_db import (
        get_models_pricing_by_provider,
        update_model_pricing_only,
    )
    from src.services.model_catalog_sync import (
        PROVIDER_FETCH_FUNCTIONS,
        ensure_provider_exists,
    )

    updated = 0
    unchanged = 0
    skipped_not_in_db = 0

    provider = ensure_provider_exists(provider_slug)
    if not provider or not provider.get("is_active"):
        raise RuntimeError(f"Provider '{provider_slug}' not found or inactive")
    provider_id = provider["id"]

    fetch_func = PROVIDER_FETCH_FUNCTIONS.get(provider_slug)
    if not fetch_func:
        raise RuntimeError(f"No fetch function for '{provider_slug}'")

    normalized_models = fetch_func() or []

    # Map existing DB models by provider_model_id -> {id, metadata}.
    existing_rows = get_models_pricing_by_provider(provider_id)
    existing_by_pmid: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        pmid = row.get("provider_model_id")
        if pmid:
            existing_by_pmid[pmid] = row

    for model in normalized_models:
        provider_model_id = model.get("provider_model_id") or model.get("id") or model.get("slug")
        if not provider_model_id:
            continue
        provider_model_id = str(provider_model_id)

        existing = existing_by_pmid.get(provider_model_id)
        if existing is None:
            # Model not in DB — price-only refresh never inserts new models.
            skipped_not_in_db += 1
            continue

        new_pricing = _extract_normalized_pricing(model, provider_slug)

        metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        stored_pricing = metadata.get("pricing_raw") if isinstance(metadata, dict) else None

        if not _pricing_differs(new_pricing, stored_pricing):
            unchanged += 1
            continue

        if dry_run:
            # Count what WOULD change without writing.
            updated += 1
            continue

        ok = update_model_pricing_only(
            model_id=existing["id"],
            pricing_raw=new_pricing,
            existing_metadata=metadata,
        )
        if ok:
            updated += 1
        else:
            # A failed write is not fatal for the run; count it as unchanged-ish
            # but surface it via logs. We do not raise so other models continue.
            logger.warning(
                f"[{provider_slug}] pricing update failed for "
                f"provider_model_id={provider_model_id}"
            )

    return {
        "provider": provider_slug,
        "models_fetched": len(normalized_models),
        "prices_updated": updated,
        "prices_unchanged": unchanged,
        "skipped_not_in_db": skipped_not_in_db,
    }


def refresh_all_prices(dry_run: bool = False) -> dict[str, Any]:
    """
    Refresh prices for ALL providers in ``PROVIDER_FETCH_FUNCTIONS``.

    This runs provider-by-provider (memory bounded — nothing is accumulated
    across providers) and wraps EACH provider in try/except so one failing
    provider never aborts the whole run.

    NOTE: This function performs SYNCHRONOUS network/DB I/O. The scheduler runs
    it inside a worker thread (``asyncio.to_thread``) so it never blocks the
    event loop. It deliberately does NOT call ``warm_caches_after_sync`` or any
    full-catalog cache rebuild.

    Args:
        dry_run: If True, compute what WOULD change without writing.

    Returns:
        Summary dict::

            {
              "success": bool,            # True if no provider raised
              "dry_run": bool,
              "providers_checked": int,
              "providers_failed": int,
              "prices_updated": int,
              "prices_unchanged": int,
              "errors": [{"provider": str, "error": str}, ...],
              "duration_seconds": float,
            }
    """
    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

    start = time.time()

    providers_checked = 0
    providers_failed = 0
    total_updated = 0
    total_unchanged = 0
    errors: list[dict[str, str]] = []

    provider_slugs = list(PROVIDER_FETCH_FUNCTIONS.keys())

    logger.info(f"Price refresh starting (dry_run={dry_run}) for {len(provider_slugs)} providers")

    for provider_slug in provider_slugs:
        try:
            result = _refresh_provider_prices(provider_slug, dry_run=dry_run)
            providers_checked += 1
            total_updated += result["prices_updated"]
            total_unchanged += result["prices_unchanged"]
            logger.info(
                f"[{provider_slug}] price refresh: "
                f"updated={result['prices_updated']} "
                f"unchanged={result['prices_unchanged']} "
                f"fetched={result['models_fetched']} "
                f"new_skipped={result['skipped_not_in_db']}"
            )
        except Exception as e:  # per-provider isolation
            providers_failed += 1
            errors.append({"provider": provider_slug, "error": str(e)})
            logger.warning(f"[{provider_slug}] price refresh failed (non-fatal): {e}")

    duration = time.time() - start

    summary = {
        "success": providers_failed == 0,
        "dry_run": dry_run,
        "providers_checked": providers_checked,
        "providers_failed": providers_failed,
        "prices_updated": total_updated,
        "prices_unchanged": total_unchanged,
        "errors": errors,
        "duration_seconds": round(duration, 2),
    }

    logger.info(
        f"Price refresh complete: updated={total_updated} unchanged={total_unchanged} "
        f"checked={providers_checked} failed={providers_failed} "
        f"duration={duration:.2f}s dry_run={dry_run}"
    )

    return summary
