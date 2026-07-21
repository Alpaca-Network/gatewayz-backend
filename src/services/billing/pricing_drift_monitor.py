"""Nightly pricing-drift monitor — catches margin leaks before they bill.

We charge inference at ``catalog_price * Config.PRICING_MARKUP``. If a
provider raises its price and our catalog goes stale (sync lag, a provider
that stopped reporting pricing, a broken cross-reference match, etc.), we
could end up billing *below* the provider's real cost — a silent margin leak
that compounds with every request.

This module audits every active model of every active (ENABLED) provider:
for each model it compares our billed price (catalog price with markup
applied) against the current OpenRouter reference price for the same
model. If the billed price is still below the reference even *with* markup
applied, the model is flagged as DRIFT. Models with no catalog price at all
(None/0) are flagged as ``unpriced`` — a distinct, often worse, failure mode
(free inference).

Read-only / side-effect-free: this module only reads pricing data and
returns a report. It never mutates prices, models, or providers. Callers
(the admin endpoint, the nightly scheduler) decide what to do with the
report — alert, page, log — but this module never acts on it.

Reuses the existing pricing infrastructure instead of re-deriving pricing
logic:
  - ``src.db.providers_db.get_active_provider_slugs`` — which providers matter
  - ``src.db.models_catalog_db.get_models_by_provider_slug`` — active models
    per provider (same query used by the catalog itself)
  - ``src.services.pricing.pricing_lookup._resolve_pricing_from_db`` — the
    shared DB pricing resolver used by both display and billing (checks the
    legacy ``model_pricing`` table, then ``metadata.pricing_raw``)
  - ``src.services.pricing.pricing_lookup._build_openrouter_pricing_index`` /
    ``_get_cross_reference_pricing`` — the same OpenRouter-catalog
    cross-reference (incl. ``OPENROUTER_PROVIDER_ALIASES``) used to price
    gateway providers during catalog sync

All prices are per-token USD, matching the canonical format used throughout
the billing pipeline (see the module docstring in ``pricing_lookup.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.config import Config
from src.db.models_catalog_db import get_models_by_provider_slug
from src.db.providers_db import get_active_provider_slugs
from src.services.pricing.pricing_lookup import (
    _build_openrouter_pricing_index,
    _get_cross_reference_pricing,
    _resolve_pricing_from_db,
)

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    """Best-effort numeric coercion. Returns None for missing/invalid values."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN guard (NaN != NaN)
        return None
    return f


def _our_catalog_pricing(model_id: str, model_name: str | None) -> dict[str, str] | None:
    """Mirror pricing_lookup's DB pricing resolution for a single model.

    Delegates entirely to ``_resolve_pricing_from_db`` (the shared resolver
    also used by the live billing path), trying both the canonical
    ``provider_model_id`` and the display ``model_name`` as candidates so the
    lookup matches whichever column pricing was actually keyed on.
    """
    candidate_ids = {model_id}
    if model_name:
        candidate_ids.add(model_name)
    return _resolve_pricing_from_db(model_id, candidate_ids=candidate_ids)


def _model_identifier(model: dict[str, Any]) -> str | None:
    """Canonical model id for a catalog row — same field used as model["id"]
    in every downstream catalog response (see get_all_pricing_batch)."""
    return model.get("provider_model_id") or model.get("model_name")


def audit_pricing_drift() -> dict[str, Any]:
    """Audit active-provider catalog pricing against OpenRouter reference pricing.

    For every active model of every active (ENABLED) provider, compares our
    billed price — ``catalog_price * Config.PRICING_MARKUP`` — against the
    current OpenRouter reference price for the same model (matched via the
    same base-id + provider-alias cross-reference logic used to price gateway
    providers during catalog sync). A model is flagged as **drift** when the
    billed price is still below the reference price on either the input or
    output side, meaning we would bill below the provider's real cost even
    after applying our markup. A model with no catalog price at all (None or
    0 on both sides) is flagged as **unpriced** instead — a distinct failure
    mode (it would bill nothing) that isn't meaningfully expressed as a
    "deficit percentage".

    OpenRouter itself is skipped as a comparison target (its own catalog IS
    the reference; there is nothing to cross-check it against), mirroring
    ``enrich_model_with_pricing``'s treatment of the openrouter gateway.

    Read-only: makes no writes. Safe to call from an admin endpoint or a
    scheduled background job.

    Returns:
        {
            "checked": int,                 # total active models examined
            "drift": [                      # worst-first by deficit_pct
                {
                    "model_id": str,
                    "provider": str,
                    "our_in": float | None,  # our catalog per-token input price
                    "our_out": float | None, # our catalog per-token output price
                    "ref_in": float | None,  # OpenRouter reference input price
                    "ref_out": float | None, # OpenRouter reference output price
                    "markup": float,
                    "deficit_pct": float,    # worst-case % below reference cost
                },
                ...
            ],
            "unpriced": [
                {"model_id": str, "provider": str, "missing": [str, ...]},
                ...
            ],
            "worst_deficit_pct": float,      # 0.0 if no drift found
            "ok": bool,                      # True iff no drift AND no unpriced
        }
    """
    markup = Config.PRICING_MARKUP
    openrouter_index = _build_openrouter_pricing_index()

    checked = 0
    drift: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []

    provider_slugs = get_active_provider_slugs()

    # Only providers that actually route + bill matter for margin. ENABLED_PROVIDERS
    # gates routing (src/utils/provider_filter.py); a provider active in the DB but
    # not enabled (e.g. openrouter as dormant fallback) never bills a user, so its
    # unpriced/underpriced models are noise, not a margin risk. Filter to enabled.
    enabled = Config.ENABLED_PROVIDERS
    if enabled:
        provider_slugs = [s for s in provider_slugs if s in enabled]

    for provider_slug in provider_slugs:
        try:
            models = get_models_by_provider_slug(provider_slug, is_active_only=True)
        except Exception as e:
            logger.error(
                f"[pricing-drift] Failed to load active models for provider '{provider_slug}': {e}"
            )
            continue

        for model in models:
            model_id = _model_identifier(model)
            if not model_id:
                continue
            checked += 1

            our_pricing = _our_catalog_pricing(model_id, model.get("model_name"))
            our_in = _to_float(our_pricing.get("prompt")) if our_pricing else None
            our_out = _to_float(our_pricing.get("completion")) if our_pricing else None

            in_missing = our_in is None or our_in <= 0
            out_missing = our_out is None or our_out <= 0

            if in_missing and out_missing:
                # No usable catalog price at all — would bill $0. Distinct
                # failure mode from drift (there's no "deficit %" of nothing).
                unpriced.append(
                    {
                        "model_id": model_id,
                        "provider": provider_slug,
                        "missing": [
                            side
                            for side, missing in (("input", in_missing), ("output", out_missing))
                            if missing
                        ],
                    }
                )
                continue

            # OpenRouter's own catalog IS the reference price — nothing to
            # cross-check it against.
            if provider_slug.lower() == "openrouter":
                continue

            ref_pricing = _get_cross_reference_pricing(
                model_id, openrouter_index, provider=provider_slug
            )
            if not ref_pricing:
                # No reference available (model not on OpenRouter, or index
                # miss) — can't assess drift for this model.
                continue

            ref_in = _to_float(ref_pricing.get("prompt"))
            ref_out = _to_float(ref_pricing.get("completion"))

            deficits: list[float] = []
            if our_in is not None and ref_in is not None and ref_in > 0:
                billed_in = our_in * markup
                if billed_in < ref_in:
                    deficits.append((ref_in - billed_in) / ref_in * 100)
            if our_out is not None and ref_out is not None and ref_out > 0:
                billed_out = our_out * markup
                if billed_out < ref_out:
                    deficits.append((ref_out - billed_out) / ref_out * 100)

            if deficits:
                drift.append(
                    {
                        "model_id": model_id,
                        "provider": provider_slug,
                        "our_in": our_in,
                        "our_out": our_out,
                        "ref_in": ref_in,
                        "ref_out": ref_out,
                        "markup": markup,
                        "deficit_pct": max(deficits),
                    }
                )

    drift.sort(key=lambda d: d["deficit_pct"], reverse=True)
    worst_deficit_pct = drift[0]["deficit_pct"] if drift else 0.0

    return {
        "checked": checked,
        "drift": drift,
        "unpriced": unpriced,
        "worst_deficit_pct": worst_deficit_pct,
        "ok": not drift and not unpriced,
    }
