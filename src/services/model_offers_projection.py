"""Model→provider offers projection (Gatewayz One, Phase 1 data pipeline).

Builds the ``public.model_provider_offers`` rows the Phase 2 smart router scores
over (see :mod:`src.services.smart_router` / :mod:`src.services.smart_router_bridge`)
from the existing ``public.models`` catalog. Each catalog row is effectively a
``(model × gateway)`` offer already; this projects it into the registry shape:

  * **canonical_id** = ``provider_model_id`` — the native model id, which is shared
    by every gateway that serves the same model. This is what groups offers so the
    router has more than one provider to choose between (≈533 models on prod are
    served by >1 gateway; the rest project to a single-offer group, a safe no-op).
  * **provider_slug** = the gateway slug (``providers.slug`` via ``provider_id``).
  * **upstream_cost** = the prompt price normalized to **per-1k tokens**. The raw
    ``pricing_original_prompt`` is stored in inconsistent units across rows (some
    per-token, some per-1M), so each value is normalized with the magnitude
    heuristic ``auto_detect_format`` — self-calibrating and unit-correct per value.
  * **quality_prior** = from ``success_rate`` when present, else the 0.5 neutral.
  * **p50_ms** = ``average_response_time_ms`` when present (p95 left null — no data).

This module is pure (no I/O): it transforms rows → offer dicts. The sync shell
``scripts/project_model_provider_offers.py`` does the fetch + upsert.
"""

from __future__ import annotations

import logging

from src.utils.pricing_normalization import auto_detect_format, normalize_to_per_token

logger = logging.getLogger(__name__)

# Modalities that are not chat completions and never appear in a chat provider
# chain, so they are excluded from the router's offer set.
_NON_CHAT_MODALITIES = {"image", "audio", "video"}


def normalized_cost_per_1k(raw_price) -> float | None:
    """Normalize a raw prompt price to per-1k tokens. None if missing/zero/invalid.

    The raw value's unit is auto-detected from its magnitude (per-token / per-1k /
    per-1M) and converted to per-token, then scaled to per-1k.
    """
    if raw_price is None or raw_price == "" or str(raw_price).lower() == "none":
        return None
    try:
        fmt = auto_detect_format(raw_price)
        per_token = normalize_to_per_token(raw_price, fmt)
        if per_token is None:
            return None
        cost = float(per_token) * 1000.0
        return cost if cost > 0 else None
    except Exception:
        return None


def _quality_from_success_rate(success_rate) -> float:
    """Map a success_rate (0..1 or 0..100) to a 0..1 quality prior; 0.5 if unknown."""
    if success_rate is None or str(success_rate).lower() == "none":
        return 0.5
    try:
        v = float(success_rate)
    except (TypeError, ValueError):
        return 0.5
    if v > 1.0:  # stored as a percentage
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _to_int_or_none(value) -> int | None:
    if value is None or str(value).lower() == "none" or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_offer_rows(models: list[dict], providers_by_id: dict) -> list[dict]:
    """Project active catalog rows into deduped ``model_provider_offers`` insert rows.

    ``providers_by_id`` maps ``provider_id`` → provider dict (must contain ``slug``).
    Rows are skipped when inactive, non-chat modality, lacking a resolvable gateway
    slug or a positive prompt cost. Duplicates on ``(canonical_id, provider_slug)``
    are collapsed to the cheapest offer (satisfies the table's UNIQUE constraint).
    """
    best: dict[tuple, dict] = {}
    for m in models:
        if not m.get("is_active", True):
            continue
        modality = (m.get("modality") or "").lower()
        if modality in _NON_CHAT_MODALITIES:
            continue

        canonical_id = m.get("provider_model_id")
        if not canonical_id:
            continue

        provider = providers_by_id.get(m.get("provider_id")) or providers_by_id.get(
            str(m.get("provider_id"))
        )
        slug = (provider or {}).get("slug")
        if not slug:
            continue

        cost = normalized_cost_per_1k(m.get("pricing_original_prompt"))
        if cost is None:
            continue

        key = (canonical_id, slug)
        offer = {
            "canonical_id": canonical_id,
            "provider_slug": slug,
            "native_id": m.get("provider_model_id") or str(m.get("id")),
            "upstream_cost": round(cost, 10),
            "quality_prior": _quality_from_success_rate(m.get("success_rate")),
            "p50_ms": _to_int_or_none(m.get("average_response_time_ms")),
            "p95_ms": None,
            "is_active": True,
        }
        existing = best.get(key)
        if existing is None or offer["upstream_cost"] < existing["upstream_cost"]:
            best[key] = offer

    return list(best.values())


def offer_summary(offers: list[dict]) -> dict:
    """Counts for reporting: total offers, distinct models, multi-provider models."""
    by_canonical: dict[str, int] = {}
    for o in offers:
        by_canonical[o["canonical_id"]] = by_canonical.get(o["canonical_id"], 0) + 1
    multi = {k: n for k, n in by_canonical.items() if n > 1}
    return {
        "total_offers": len(offers),
        "distinct_models": len(by_canonical),
        "multi_provider_models": len(multi),
        "max_providers_for_one_model": max(by_canonical.values()) if by_canonical else 0,
    }
