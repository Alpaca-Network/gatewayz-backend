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


def _input_price_per_token(model_pricing) -> float | None:
    """Extract ``price_per_input_token`` from a ``model_pricing`` join.

    The join may arrive as a dict, a single-element list (Supabase embed), or
    ``None`` when the model has no pricing row. Returns the positive per-token
    price, or ``None`` when absent/zero/invalid.
    """
    if not model_pricing:
        return None
    row = model_pricing[0] if isinstance(model_pricing, list) else model_pricing
    if not isinstance(row, dict):
        return None
    try:
        v = float(row.get("price_per_input_token") or 0.0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def cost_per_1k_from_model(m: dict) -> float | None:
    """Per-1k upstream input cost for a catalog row. None if unpriced.

    Prefers the real per-provider price from the ``model_pricing`` join
    (``price_per_input_token`` is already per-token → ×1000 for per-1k), and
    falls back to the legacy ``pricing_original_prompt`` column (magnitude
    auto-detected) for rows that predate the pricing table.
    """
    per_token = _input_price_per_token(m.get("model_pricing"))
    if per_token is not None:
        cost = per_token * 1000.0
        return cost if cost > 0 else None
    return normalized_cost_per_1k(m.get("pricing_original_prompt"))


# Plausible per-token USD input price for a PAID model: ~$0.001/1M .. $1000/1M.
# Anything outside almost certainly means a unit/ingestion error (or a stale
# cached price). Such an offer must NOT win cost-routing — a near-zero price
# would both mis-route and undercharge billing — so it is dropped from the
# projection. In per-1k terms the band is [1e-6, 1.0].
_MIN_PLAUSIBLE_COST_PER_1K = 1e-6
_MAX_PLAUSIBLE_COST_PER_1K = 1.0


def is_plausible_cost_per_1k(cost_per_1k: float | None) -> bool:
    """True when a per-1k upstream cost is inside the sane pricing band.

    ``None`` (unpriced) is not plausible here — callers handle free/unpriced
    models separately before this check.
    """
    if cost_per_1k is None:
        return False
    return _MIN_PLAUSIBLE_COST_PER_1K <= cost_per_1k <= _MAX_PLAUSIBLE_COST_PER_1K


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


def build_offer_rows(
    models: list[dict], providers_by_id: dict, alias_map: dict[str, str] | None = None
) -> list[dict]:
    """Project active catalog rows into deduped ``model_provider_offers`` insert rows.

    ``providers_by_id`` maps ``provider_id`` → provider dict (must contain ``slug``).
    ``alias_map`` (lowercased native id → canonical) is applied inside the grouping
    key so curated cross-org merges take effect; pass ``None`` for pure normalization.

    Rows are skipped when inactive, non-chat modality, lacking a resolvable gateway
    slug or a positive prompt cost. The ``canonical_id`` stored on each offer is the
    cost-routing GROUP KEY (``offer_group_key``) so the same model served by several
    providers — even under different casing/separators/re-host prefixes — collapses
    into one comparable group. ``native_id`` keeps the provider-native id used to
    dispatch. Duplicates on ``(canonical_id, provider_slug)`` collapse to the cheapest
    offer (satisfies the table's UNIQUE constraint).
    """
    from src.services.model_canonicalization import offer_group_key

    best: dict[tuple, dict] = {}
    dropped_implausible = 0
    dropped_unpriced = 0
    dropped_no_provider = 0
    for m in models:
        if not m.get("is_active", True):
            continue
        modality = (m.get("modality") or "").lower()
        if modality in _NON_CHAT_MODALITIES:
            continue

        native_id = m.get("provider_model_id")
        if not native_id:
            continue

        group_key = offer_group_key(native_id, alias_map)
        if not group_key:
            continue

        provider = providers_by_id.get(m.get("provider_id")) or providers_by_id.get(
            str(m.get("provider_id"))
        )
        slug = (provider or {}).get("slug")
        if not slug:
            # No resolvable gateway for this provider_id — unroutable offer.
            dropped_no_provider += 1
            continue

        cost = cost_per_1k_from_model(m)
        if cost is None:
            dropped_unpriced += 1
            continue
        if not is_plausible_cost_per_1k(cost):
            # Garbage/stale price (unit error, cache staleness). Excluding it keeps
            # the cost router honest instead of letting a fake-cheap offer win.
            dropped_implausible += 1
            continue

        key = (group_key, slug)
        offer = {
            "canonical_id": group_key,
            "provider_slug": slug,
            "native_id": native_id,
            "upstream_cost": round(cost, 10),
            "quality_prior": _quality_from_success_rate(m.get("success_rate")),
            "p50_ms": _to_int_or_none(m.get("average_response_time_ms")),
            "p95_ms": None,
            "is_active": True,
        }
        existing = best.get(key)
        if existing is None or offer["upstream_cost"] < existing["upstream_cost"]:
            best[key] = offer

    if dropped_implausible:
        logger.warning(
            "Dropped %d offer(s) with implausible upstream price "
            "(outside [%s, %s] per 1k) — likely a unit/ingestion error or stale cache",
            dropped_implausible,
            _MIN_PLAUSIBLE_COST_PER_1K,
            _MAX_PLAUSIBLE_COST_PER_1K,
        )
    if dropped_unpriced or dropped_no_provider:
        # Logged (not silently dropped) so catalog shrinkage to routable+priced
        # reality is observable — North Star §5.
        logger.info(
            "Offer projection excluded %d model(s) as unroutable: %d unpriced, "
            "%d with no resolvable gateway",
            dropped_unpriced + dropped_no_provider,
            dropped_unpriced,
            dropped_no_provider,
        )
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


def filter_multi_provider(offers: list[dict]) -> list[dict]:
    """Keep only offers for models served by more than one gateway (pure)."""
    counts: dict[str, int] = {}
    for o in offers:
        counts[o["canonical_id"]] = counts.get(o["canonical_id"], 0) + 1
    return [o for o in offers if counts[o["canonical_id"]] > 1]


# --------------------------------------------------------------------------- #
# I/O shell (used by the CLI and the scheduled-sync hook). Kept thin; the pure
# transform above is what carries the unit-tested logic.
# --------------------------------------------------------------------------- #

_PAGE = 1000
_UPSERT_BATCH = 500
_MODEL_COLS = (
    "id,provider_id,provider_model_id,pricing_original_prompt,"
    "success_rate,average_response_time_ms,is_active,modality,"
    "model_pricing(price_per_input_token,price_per_output_token)"
)


def _providers_by_id(client) -> dict:
    resp = client.table("providers").select("id,slug,name").execute()
    out: dict = {}
    for p in getattr(resp, "data", None) or []:
        out[p["id"]] = p
        out[str(p["id"])] = p  # tolerate int/str provider_id
    return out


def _fetch_active_models(client, limit: int | None) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table("models")
            .select(_MODEL_COLS)
            .eq("is_active", True)
            .range(start, start + _PAGE - 1)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if limit and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < _PAGE:
            return rows
        start += _PAGE


# Below this many freshly-projected offers we skip the stale-row sweep, so a
# transient near-empty projection (e.g. a failed catalog fetch) can never wipe the
# live offers table.
_STALE_SWEEP_FLOOR = 50


def _upsert_offers(client, offers: list[dict]) -> tuple[int, str]:
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).isoformat()
    written = 0
    for i in range(0, len(offers), _UPSERT_BATCH):
        batch = [dict(o, updated_at=stamp) for o in offers[i : i + _UPSERT_BATCH]]
        client.table("model_provider_offers").upsert(
            batch, on_conflict="canonical_id,provider_slug"
        ).execute()
        written += len(batch)
    return written, stamp


def _delete_stale_offers(client, stamp: str, written: int) -> int:
    """Delete offers this projection run did not touch (older key scheme / dropped).

    Every current offer was upserted with ``updated_at == stamp``; anything with an
    older stamp is stale. Guarded by a floor so a near-empty projection is a no-op.
    """
    if written < _STALE_SWEEP_FLOOR:
        logger.warning(
            "offers projection wrote only %d rows (< floor %d); skipping stale sweep",
            written,
            _STALE_SWEEP_FLOOR,
        )
        return 0
    resp = client.table("model_provider_offers").delete().lt("updated_at", stamp).execute()
    return len(getattr(resp, "data", None) or [])


def refresh_offers_projection(
    *, only_multi: bool = False, dry_run: bool = False, limit: int | None = None
) -> dict:
    """Fetch the catalog, build offers, and upsert them. Returns {summary, offers}.

    The single entry point for both ``scripts/project_model_provider_offers.py`` and
    the scheduled-sync hook. Idempotent (upserts on the unique key).
    """
    from src.config.supabase_config import get_supabase_client
    from src.services.model_canonicalization import load_alias_map

    client = get_supabase_client()
    providers = _providers_by_id(client)
    models = _fetch_active_models(client, limit)
    offers = build_offer_rows(models, providers, load_alias_map())
    if only_multi:
        offers = filter_multi_provider(offers)

    summary = offer_summary(offers)
    summary["models_scanned"] = len(models)
    summary["dry_run"] = dry_run
    summary["only_multi"] = only_multi
    if not dry_run:
        written, stamp = _upsert_offers(client, offers)
        summary["rows_written"] = written
        # Sweep offers left over from the previous (raw-id) key scheme or dropped
        # models, unless this run projected suspiciously few rows.
        summary["stale_deleted"] = _delete_stale_offers(client, stamp, written)
    return {"summary": summary, "offers": offers}
