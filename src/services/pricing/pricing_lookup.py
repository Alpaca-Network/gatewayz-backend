"""
Pricing Lookup Service
Provides manual pricing lookup for providers that don't expose pricing via API

CANONICAL PRICING FORMAT
------------------------
All pricing values throughout this service and the billing pipeline are stored and
returned in **per-token** format — i.e., cost per single token (e.g., 0.000000055 USD).

Source-specific raw formats are converted to per-token by pricing_normalization.py:
  - OpenRouter API  -> already per-token   (PricingFormat.PER_TOKEN)
  - manual_pricing.json (non-OpenRouter) -> per-1M tokens (PricingFormat.PER_1M_TOKENS)
  - per-1K provider APIs                  -> per-1K tokens (PricingFormat.PER_1K_TOKENS)

If you add a new pricing source, use normalize_pricing_dict() with the correct
PricingFormat constant before returning values from this module.
"""

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Manual pricing seed: src/data/manual_pricing.json (this file is src/services/pricing/).
_MANUAL_PRICING_PATH = Path(__file__).resolve().parents[2] / "data" / "manual_pricing.json"


def validate_pricing_value(value: Any, field: str, model_id: str = "") -> str:
    """
    Validate a single pricing value.

    Ensures the value is numeric and non-negative. If invalid, logs a warning
    and returns "0" as a safe fallback.

    Args:
        value: The raw pricing value (str, int, float, or other).
        field: Field name used in the warning message (e.g. "prompt").
        model_id: Optional model identifier for more useful log messages.

    Returns:
        A string representation of the validated value, or "0" if invalid.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        logger.warning(
            f"Pricing field '{field}' for model '{model_id}' is not numeric "
            f"(got {value!r}); defaulting to 0"
        )
        return "0"

    import math

    if not math.isfinite(numeric) or numeric < 0:
        logger.warning(
            f"Pricing field '{field}' for model '{model_id}' is invalid "
            f"({numeric}); defaulting to 0"
        )
        return "0"

    return str(value)


# Gateway providers that route to underlying providers (OpenAI, Anthropic, etc.)
# These need cross-reference pricing from OpenRouter if no manual pricing exists
# Models without valid pricing will be filtered out to avoid appearing as "free"
GATEWAY_PROVIDERS = {
    "akash",
    "alibaba-cloud",
    "anthropic",  # Direct Anthropic API - needs cross-reference for model ID matching
    "clarifai",
    "cloudflare-workers-ai",
    "deepinfra",
    "featherless",
    "fireworks",
    "groq",
    "together",
    # Direct open-weight providers whose /models API returns no pricing — must be
    # priced (via OpenRouter cross-reference) or hidden, never shown as free.
    "moonshot",
    "minimax",
    "deepseek",
    "xiaomi",
}

# Map our provider slug -> the org prefix OpenRouter uses, for precise
# cross-reference matching (e.g. our "moonshot/kimi-k3" == OpenRouter's
# "moonshotai/kimi-k3"). Adding a provider here is all it takes to auto-price
# its models from OpenRouter on every sync — the scalable path. Providers whose
# slug already matches OpenRouter's org need no entry (base-id match still works).
OPENROUTER_PROVIDER_ALIASES: dict[str, str] = {
    "moonshot": "moonshotai",
    "alibaba": "qwen",
    "alibaba-cloud": "qwen",
    "google-vertex": "google",
}

# Pricing lookup tier order (checked in sequence, first match wins)
PRICING_TIERS = ["database", "manual_json", "cross_reference"]

# Cache for pricing data
_pricing_cache: dict[str, Any] | None = None
_pricing_cache_lock = threading.Lock()
# Timestamp (monotonic seconds) of when _pricing_cache was last populated.
# None means the cache has never been loaded or was explicitly invalidated.
_pricing_cache_timestamp: float | None = None
# How long (in seconds) the in-memory pricing cache is considered fresh.
# After this interval the next access will clear the cache so it reloads from disk.
PRICING_CACHE_TTL: float = 15 * 60  # 15 minutes

# Cache for OpenRouter pricing index (O(1) lookups)
_openrouter_pricing_index: dict[str, dict] | None = None


def load_manual_pricing() -> dict[str, Any]:
    """Load the manual pricing seed from ``src/data/manual_pricing.json``.

    This is the tier-3 fallback in :func:`get_model_pricing` for providers whose
    upstream ``/models`` API returns no pricing (e.g. OpenAI, Anthropic). Without
    it, those models sync with ``None`` pricing and get filtered out of the served
    catalog (and blocked at the inference gate). Model keys are lowercased at load
    time so lookups are O(1) and case-insensitive. Result is cached in-memory for
    ``PRICING_CACHE_TTL``; thread-safe. On any read/parse error, caches and returns
    an empty dict so callers degrade gracefully to the next tier.
    """
    global _pricing_cache, _pricing_cache_timestamp

    now = time.monotonic()
    with _pricing_cache_lock:
        if (
            _pricing_cache is not None
            and _pricing_cache_timestamp is not None
            and (now - _pricing_cache_timestamp) < PRICING_CACHE_TTL
        ):
            return _pricing_cache

        try:
            with open(_MANUAL_PRICING_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load manual pricing from {_MANUAL_PRICING_PATH}: {e}")
            _pricing_cache = {}
            _pricing_cache_timestamp = now
            return _pricing_cache

        normalized: dict[str, Any] = {}
        for gateway, models in raw.items():
            gw = str(gateway).lower()
            if isinstance(models, dict):
                normalized[gw] = {str(k).lower(): v for k, v in models.items()}
            else:
                normalized[gw] = models

        _pricing_cache = normalized
        _pricing_cache_timestamp = now
        return _pricing_cache


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
        from src.utils.pricing_normalization import get_provider_format, normalize_pricing_dict

        pricing_data = load_manual_pricing()

        if not pricing_data:
            return None

        gateway_lower = gateway.lower()

        if gateway_lower not in pricing_data:
            return None

        gateway_pricing = pricing_data[gateway_lower]

        raw_pricing = None
        # Keys are pre-lowercased at load time, so a single O(1) lookup suffices
        model_id_lower = model_id.lower()
        if model_id_lower in gateway_pricing:
            raw_pricing = gateway_pricing[model_id_lower]
        elif model_id in gateway_pricing:
            # Fallback for any non-lowercased entry (e.g. metadata key)
            raw_pricing = gateway_pricing[model_id]

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


def get_image_pricing(provider: str, model: str) -> tuple[float, bool] | None:
    """
    Get per-image pricing from manual_pricing.json for image generation models.

    Looks up the "image_pricing" section of manual_pricing.json. Returns the
    per-image cost in USD and a flag indicating whether the price came from a
    provider-level default rather than an exact model match.  Returns None if
    not found (caller should fall back to hardcoded defaults).

    The lookup order is:
      1. Exact model match under the provider key  (is_fallback=False)
      2. Provider-level "default" entry             (is_fallback=True)
      3. None (not found)

    Args:
        provider: Image generation provider (e.g. "deepinfra", "fal", "google-vertex")
        model: Model name (e.g. "stable-diffusion-3.5-large", "flux/schnell")

    Returns:
        Tuple of (cost_per_image, is_fallback), or None if no config-driven pricing
        is available.
    """
    try:
        pricing_data = load_manual_pricing()
        if not pricing_data:
            return None

        image_pricing = pricing_data.get("image_pricing")
        if not image_pricing or not isinstance(image_pricing, dict):
            return None

        provider_lower = provider.lower()
        provider_section = image_pricing.get(provider_lower)
        if not provider_section or not isinstance(provider_section, dict):
            return None

        # Try exact model match first (all keys are pre-lowercased at load time)
        is_fallback = False
        model_lower = model.lower()
        entry = provider_section.get(model_lower)
        if entry is None:
            entry = provider_section.get(model)

        # Fall back to provider-level default for unknown models
        if entry is None:
            entry = provider_section.get("default")
            is_fallback = True

        if entry is None:
            return None

        if isinstance(entry, dict):
            per_image = entry.get("per_image")
            if per_image is not None:
                return float(per_image), is_fallback
            return None
        else:
            # Support bare numeric values for simpler entries
            return float(entry), is_fallback

    except Exception as e:
        logger.error(f"Error loading image pricing for {provider}/{model}: {e}")
        return None


def _is_building_catalog() -> bool:
    """Check if we're currently building the model catalog to avoid circular imports"""
    try:
        from src.services.models import _is_building_catalog as check_building

        return check_building()
    except ImportError:
        return False


_unpriced_models: set[str] = set()
_unpriced_lock = threading.Lock()

# Shared across workers. A per-process set only ever showed whichever worker
# happened to answer /health/catalog/unpriced, and the sync that discovered the
# drop is rarely the worker serving the request — so the endpoint could read 0
# while models were being dropped. The local set is kept as a fallback for when
# Redis is unavailable.
UNPRICED_MODELS_KEY = "gw:models:unpriced"
_UNPRICED_TTL_SECONDS = 7 * 24 * 3600


def _unpriced_redis():
    try:
        from src.config.redis_config import get_redis_client, is_redis_available

        client = get_redis_client()
        return client if (client and is_redis_available()) else None
    except Exception:
        return None


def record_unpriced_model(model_id: str) -> None:
    """Remember a model that was dropped for having no price."""
    if not model_id:
        return
    with _unpriced_lock:
        _unpriced_models.add(str(model_id))

    client = _unpriced_redis()
    if client:
        try:
            client.sadd(UNPRICED_MODELS_KEY, str(model_id))
            # Expire the whole set so a model that later gets priced does not
            # linger in the report forever.
            client.expire(UNPRICED_MODELS_KEY, _UNPRICED_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not record unpriced model in Redis: %s", e)


def get_unpriced_models() -> list[str]:
    """Models dropped from the catalog for want of a price.

    Exposed so a launch that lands without pricing is visible the same day
    instead of being discovered weeks later, which is how Claude Opus 5 went
    missing.
    """
    client = _unpriced_redis()
    if client:
        try:
            members = client.smembers(UNPRICED_MODELS_KEY) or set()
            return sorted(m.decode() if isinstance(m, bytes) else str(m) for m in members)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not read unpriced models from Redis: %s", e)

    with _unpriced_lock:
        return sorted(_unpriced_models)


def clear_unpriced_models() -> None:
    """Reset the set — called at the start of a full catalog sync."""
    with _unpriced_lock:
        _unpriced_models.clear()

    client = _unpriced_redis()
    if client:
        try:
            client.delete(UNPRICED_MODELS_KEY)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Could not clear unpriced models in Redis: %s", e)


def _load_price_reference_catalog() -> list[dict]:
    """Load the aggregator catalog used purely as a price reference.

    Deliberately reads models regardless of ``is_active``. Being *listed* and
    being a *price reference* are different jobs: OpenRouter is delisted as
    supply (North Star §5 bars an aggregator as primary supply, and
    ENABLED_PROVIDERS blocks routing to it), but its catalog is the only place
    that carries prices for models whose own provider publishes none — OpenAI,
    Anthropic and xAI all return catalogs with no pricing at all.

    Coupling the two is what silently broke intake: delisting OpenRouter as
    supply emptied this index, so newly released models — Claude Opus 5 among
    them — could no longer acquire a price and were filtered out of the catalog
    entirely. Nothing routes here; only prices are read.
    """
    try:
        from src.db.models_catalog_db import get_models_by_gateway_for_catalog

        rows = get_models_by_gateway_for_catalog(gateway_slug="openrouter", include_inactive=True)
    except Exception as e:
        logger.warning("Price reference catalog unavailable: %s", e)
        return []

    catalog: list[dict] = []
    for row in rows or []:
        metadata = row.get("metadata") or {}
        pricing = metadata.get("pricing_raw") if isinstance(metadata, dict) else None
        if not isinstance(pricing, dict) or not pricing:
            continue
        model_id = row.get("provider_model_id") or row.get("model_name")
        if model_id:
            catalog.append({"id": model_id, "pricing": pricing})

    logger.info("Price reference catalog: %d priced models loaded", len(catalog))
    return catalog


def _build_openrouter_pricing_index() -> dict[str, dict]:
    """Build an O(1) lookup index from OpenRouter models.

    Called once per catalog build cycle. Returns a dict keyed by multiple
    aliases for each model (full id, base id, lowercase variants) so that
    cross-reference lookups are O(1) instead of O(N).
    """
    global _openrouter_pricing_index
    if _openrouter_pricing_index is not None:
        return _openrouter_pricing_index

    # CRITICAL: when we're inside the catalog rebuild path, calling
    # get_cached_models("openrouter") would re-enter transform_db_models_batch
    # → _build_openrouter_pricing_index → … (infinite recursion through the
    # rebuild lock). Skip the index build entirely; cross-reference lookups
    # are already a no-op while building (see _get_cross_reference_pricing
    # at the _is_building_catalog() guard) so an empty index is harmless.
    if _is_building_catalog():
        return {}

    index: dict[str, dict] = {}
    try:
        openrouter_models = _load_price_reference_catalog()
        for model in openrouter_models:
            if not isinstance(model, dict):
                continue
            pricing = model.get("pricing")
            if not pricing:
                continue
            model_id = model.get("id", "")
            base_id = model_id.split("/")[-1] if "/" in model_id else model_id

            for key in (model_id, model_id.lower(), base_id, base_id.lower()):
                if key:
                    index[key] = pricing

        _openrouter_pricing_index = index
    except Exception as e:
        logger.warning(f"Failed to build OpenRouter pricing index: {e}")
        _openrouter_pricing_index = {}

    return _openrouter_pricing_index


def invalidate_openrouter_pricing_index() -> None:
    """Invalidate the OpenRouter pricing index. Call when the OpenRouter cache is refreshed."""
    global _openrouter_pricing_index
    _openrouter_pricing_index = None


# Anthropic and OpenRouter spell the same model differently: Anthropic ships
# "claude-opus-4-6" and dated snapshots like "claude-haiku-4-5-20251001", while
# OpenRouter lists "claude-opus-4.6" and "claude-haiku-4.5". normalize_model_name
# cannot bridge them — it maps "." to "p" but leaves "-" alone, so "4.6" becomes
# "4p6" and "4-6" stays "4-6" and the two never meet. It also drives provider
# dispatch, so widening it there risks mis-routing an inference call.
#
# These candidates are therefore built only for the price lookup. Four Anthropic
# models sat unpriced — and so unlistable — purely because of this, with the
# price sitting in the index under a dotted name.
_DATE_SUFFIX = re.compile(r"-\d{8}$")
_VERSION_SEGMENT = re.compile(r"(?<=\d)-(?=\d)")


def _cross_reference_candidates(model_id: str, base_model_id: str) -> list[str]:
    """Lookup keys to try, most literal first."""
    candidates: list[str] = []

    def add(value: str) -> None:
        for form in (value, value.lower()):
            if form and form not in candidates:
                candidates.append(form)

    for value in (model_id, base_model_id):
        if not value:
            continue
        add(value)
        # "claude-haiku-4-5-20251001" -> "claude-haiku-4-5"
        undated = _DATE_SUFFIX.sub("", value)
        add(undated)
        # "claude-opus-4-6" -> "claude-opus-4.6" (a hyphen BETWEEN DIGITS only,
        # so "gpt-4" and "claude-3-opus" are untouched)
        add(_VERSION_SEGMENT.sub(".", undated))

    return candidates


def _get_cross_reference_pricing(
    model_id: str,
    openrouter_index: dict[str, dict] | None = None,
    provider: str | None = None,
) -> dict[str, str] | None:
    """
    Get pricing for a provider model by cross-referencing OpenRouter's catalog.

    Extracts the underlying model ID and looks up its pricing from the OpenRouter
    pricing index. When ``provider`` is supplied and has an OpenRouter org alias
    (OPENROUTER_PROVIDER_ALIASES), the fully-qualified aliased id is tried first
    for a precise match (e.g. our "moonshot/kimi-k3" -> OpenRouter
    "moonshotai/kimi-k3") before falling back to base-id matching.

    Uses an O(1) index lookup when `openrouter_index` is provided (batch path).
    Falls back to building the index on demand for single-model lookups.

    Args:
        model_id: Model ID from the provider (e.g., "openai/gpt-4o", "gpt-4o-mini")
        openrouter_index: Pre-built pricing index from _build_openrouter_pricing_index().
                          Pass None to have the function build/fetch the index itself.
        provider: Optional provider slug, used for org-alias precise matching.

    Returns:
        Pricing dictionary (normalized to per-token format) or None if not found
    """
    # Avoid circular dependency during catalog building
    if _is_building_catalog():
        return None

    try:
        from src.utils.pricing_normalization import PricingFormat, normalize_pricing_dict

        # Use the provided index or build it on demand (single-model fallback path)
        index = (
            openrouter_index if openrouter_index is not None else _build_openrouter_pricing_index()
        )
        if not index:
            return None

        # Extract the base model name from the gateway model ID
        # e.g., "openai/gpt-4o" -> "gpt-4o", "anthropic/claude-3-opus" -> "claude-3-opus"
        base_model_id = model_id.split("/")[-1] if "/" in model_id else model_id

        # --- Precise: provider org-alias fully-qualified match (lowest collision risk) ---
        if provider:
            alias = OPENROUTER_PROVIDER_ALIASES.get(provider.lower())
            if alias:
                aliased = f"{alias}/{base_model_id}"
                for candidate in (aliased, aliased.lower()):
                    if candidate in index:
                        return normalize_pricing_dict(index[candidate], PricingFormat.PER_TOKEN)

        # --- O(1) exact-match attempts ---
        # OpenRouter's API returns prices already in per-token format (e.g. 0.000000055),
        # so we must normalize using PER_TOKEN — not PER_1M_TOKENS.
        # This is the canonical format used everywhere in the billing pipeline.
        # See PROVIDER_PRICING_FORMATS["openrouter"] in pricing_normalization.py.
        for candidate in _cross_reference_candidates(model_id, base_model_id):
            if candidate and candidate in index:
                return normalize_pricing_dict(index[candidate], PricingFormat.PER_TOKEN)

        # --- Versioned-suffix fallback: scan only the (small) set of index keys ---
        # e.g., "claude-3-opus" should match "claude-3-opus-20240229" but NOT "claude-3-opus-mini"
        base_lower = base_model_id.lower()
        for key, pricing in index.items():
            if not key.startswith(base_lower):
                continue
            suffix = key[len(base_lower) :]
            if not suffix or (
                suffix.startswith("-") and len(suffix) > 1 and suffix[1:].replace("-", "").isdigit()
            ):
                return normalize_pricing_dict(pricing, PricingFormat.PER_TOKEN)

        return None

    except Exception as e:
        logger.debug(f"Error getting cross-reference pricing for {model_id}: {e}")
        return None


def _resolve_pricing_from_db(
    model_id: str,
    candidate_ids: set[str] | None = None,
) -> dict[str, str] | None:
    """Shared database pricing resolver used by both display and billing.

    Checks two sources per candidate in order:
    1. model_pricing table (JOIN) — legacy pricing storage
    2. metadata.pricing_raw — current sync storage location

    For each candidate, tries ``model_name`` first, then ``provider_model_id``.

    Args:
        model_id: Primary model identifier.
        candidate_ids: Optional expanded set of IDs to try (normalised,
            aliased, etc.).  Defaults to ``{model_id}`` when *None*.

    Returns:
        Pricing dictionary in per-token string format, or *None*.
    """
    if candidate_ids is None:
        candidate_ids = {model_id}

    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        select_cols = (
            "id, model_name, metadata, model_pricing(price_per_input_token, price_per_output_token)"
        )

        for candidate in candidate_ids:
            if not candidate:
                continue

            # --- try model_name match first, then provider_model_id ---
            for column in ("model_name", "provider_model_id"):
                try:
                    result = (
                        client.table("models")
                        .select(select_cols)
                        .eq(column, candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )
                except Exception:
                    continue

                if not result.data or not result.data[0]:
                    continue

                row = result.data[0]

                # Source 1: model_pricing table (legacy)
                if row.get("model_pricing"):
                    pricing_data = row["model_pricing"]
                    if isinstance(pricing_data, list):
                        pricing_data = pricing_data[0] if pricing_data else None

                    if pricing_data:
                        prompt_price = pricing_data.get("price_per_input_token")
                        completion_price = pricing_data.get("price_per_output_token")

                        if prompt_price is not None and completion_price is not None:
                            logger.debug(
                                "[DB] Pricing for %s via %s=%s (model_pricing table)",
                                model_id,
                                column,
                                candidate,
                            )
                            return {
                                "prompt": validate_pricing_value(prompt_price, "prompt", model_id),
                                "completion": validate_pricing_value(
                                    completion_price, "completion", model_id
                                ),
                                "request": "0",
                                "image": "0",
                                "source": "database",
                            }

                # Source 2: metadata.pricing_raw (current sync storage)
                metadata = row.get("metadata")
                if isinstance(metadata, dict):
                    pricing_raw = metadata.get("pricing_raw")
                    if isinstance(pricing_raw, dict):
                        prompt_price = pricing_raw.get("prompt")
                        completion_price = pricing_raw.get("completion")

                        if prompt_price is not None and completion_price is not None:
                            logger.debug(
                                "[DB] Pricing for %s via %s=%s (metadata.pricing_raw)",
                                model_id,
                                column,
                                candidate,
                            )
                            return {
                                "prompt": validate_pricing_value(prompt_price, "prompt", model_id),
                                "completion": validate_pricing_value(
                                    completion_price, "completion", model_id
                                ),
                                "request": validate_pricing_value(
                                    pricing_raw.get("request", "0"), "request", model_id
                                ),
                                "image": validate_pricing_value(
                                    pricing_raw.get("image", "0"), "image", model_id
                                ),
                                "source": "database",
                            }

        return None

    except Exception as e:
        logger.error(f"Database pricing lookup failed for {model_id}: {e}")
        return None


# Backward-compat alias — tests and internal callers that reference the old name
def _get_pricing_from_database(model_id: str) -> dict[str, str] | None:
    return _resolve_pricing_from_db(model_id)


def get_all_pricing_batch() -> dict[str, dict]:
    """Fetch all model pricing in a single database query.

    Returns a dict keyed by ``provider_model_id`` (the canonical API identifier
    used as ``model["id"]`` in downstream catalog responses) so enrichment
    callers can do O(1) lookups instead of one Supabase HTTP call per model.

    Returns:
        {provider_model_id: {"prompt": "...", "completion": "...", "request": "...", "image": "...", "source": "..."}}
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Paginate to avoid Supabase's default 1000-row limit truncating results
        all_rows: list[dict] = []
        page_size = 1000
        offset = 0
        max_pages = 100  # Safety cap: 100k models max
        deadline = time.monotonic() + 30  # 30-second wall-clock deadline
        while True:
            result = (
                client.table("models")
                .select(
                    "model_name, provider_model_id, metadata, "
                    "model_pricing(price_per_input_token, price_per_output_token)"
                )
                .eq("is_active", True)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = result.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
            max_pages -= 1
            if max_pages <= 0 or time.monotonic() > deadline:
                logger.warning(f"Pricing batch fetch hit safety limit at {len(all_rows)} rows")
                break

        pricing_map: dict[str, dict] = {}
        for row in all_rows:
            # Key by provider_model_id — the canonical API identifier used as
            # `model["id"]` in every downstream catalog response. model_name is
            # only a display label and is not unique, so it cannot be the key.
            key = row.get("provider_model_id")
            if not key:
                continue

            # Source 1: model_pricing JOIN (dedicated pricing table)
            mp = row.get("model_pricing")
            if mp and isinstance(mp, list) and len(mp) > 0:
                mp = mp[0]
            if (
                mp
                and isinstance(mp, dict)
                and (mp.get("price_per_input_token") or mp.get("price_per_output_token"))
            ):
                pricing_map[key] = {
                    "prompt": validate_pricing_value(
                        mp.get("price_per_input_token", 0), "prompt", key
                    ),
                    "completion": validate_pricing_value(
                        mp.get("price_per_output_token", 0), "completion", key
                    ),
                    "request": "0",
                    "image": "0",
                    "source": "database_batch",
                }
                continue

            # Source 2: metadata.pricing_raw (inline sync path)
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict):
                pricing_raw = metadata.get("pricing_raw") or metadata.get("pricing") or {}
                if isinstance(pricing_raw, dict) and (
                    pricing_raw.get("prompt") is not None
                    or pricing_raw.get("completion") is not None
                ):
                    pricing_map[key] = {
                        "prompt": validate_pricing_value(
                            pricing_raw.get("prompt", 0), "prompt", key
                        ),
                        "completion": validate_pricing_value(
                            pricing_raw.get("completion", 0), "completion", key
                        ),
                        "request": validate_pricing_value(
                            pricing_raw.get("request", 0), "request", key
                        ),
                        "image": validate_pricing_value(pricing_raw.get("image", 0), "image", key),
                        "source": "metadata_batch",
                    }

        logger.info(f"Batch pricing fetch: loaded {len(pricing_map)} models in one query")
        return pricing_map

    except Exception as e:
        logger.error(f"Failed to batch fetch pricing: {e}")
        return {}


def enrich_model_with_pricing(
    model_data: dict[str, Any],
    gateway: str,
    pricing_batch: dict[str, dict] | None = None,
    openrouter_index: dict[str, dict] | None = None,
) -> dict[str, Any] | None:
    """
    Enrich model data with pricing information.

    Phase 2 Update: Database-first approach with JSON fallback.

    Lookup priority:
    1. Pre-fetched batch pricing map (when `pricing_batch` is supplied — avoids per-model DB round-trips)
    2. Database per-model query (fallback when no batch map provided)
    3. Manual pricing JSON
    4. Cross-reference from OpenRouter (for gateway providers, uses O(1) index when available)

    Args:
        model_data: Model dictionary
        gateway: Gateway name
        pricing_batch: Optional pre-fetched {model_name: pricing_dict} map from
                       get_all_pricing_batch(). When provided the per-model database
                       call is skipped entirely, eliminating the N+1 query problem.
        openrouter_index: Optional pre-built OpenRouter pricing index from
                          _build_openrouter_pricing_index(). When provided the
                          cross-reference lookup is O(1) instead of O(N).

    Returns:
        Enhanced model dictionary with pricing, or None if no pricing found for gateway providers
    """
    model_id = model_data.get("id")
    if not model_id:
        return model_data

    # Advertise capabilities alongside pricing. Agent tools need to know which
    # models take tools / cache_control before they send a request; without it
    # the only way to find out is to trigger an error.
    try:
        from src.services.model_capability_surface import enrich_model_with_capabilities

        enrich_model_with_capabilities(model_data)
    except Exception:
        # Never let capability metadata break catalog assembly.
        pass

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

        # 3-tier pricing fallback — checked in order defined by PRICING_TIERS:
        #   Tier 1 "database"        — DB models table (batch map or per-model query)
        #   Tier 2 "manual_json"     — static manual_pricing.json bundled with the service
        #   Tier 3 "cross_reference" — OpenRouter catalog lookup (gateway providers only)
        # First tier that returns non-None pricing wins; subsequent tiers are skipped.

        # PHASE 2: Try database pricing — prefer the pre-fetched batch map (Fix 1/3)
        db_pricing: dict[str, str] | None = None
        if pricing_batch is not None:
            # Batch was provided (even if empty) — use O(1) lookup, never fall
            # back to per-model DB queries (the batch already represents the full DB).
            batch_entry = pricing_batch.get(model_id)
            if batch_entry:
                db_pricing = {k: v for k, v in batch_entry.items() if k != "source"}
        else:
            # No batch provided — per-model DB query (legacy path for single-model callers)
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

        # Tier 4 — cross-reference with OpenRouter (universal fallback). Any provider
        # whose models OpenRouter also lists gets exact pricing here even without a
        # manual/DB entry. Only runs after the DB and manual tiers miss. Skip
        # openrouter itself (its own catalog is the source). This is the scalable
        # path: a new direct provider needs no per-model pricing work — if OpenRouter
        # lists the model, it is priced automatically on every sync.
        if gateway_lower != "openrouter":
            cross_ref_pricing = _get_cross_reference_pricing(
                model_id, openrouter_index, provider=gateway_lower
            )
            if cross_ref_pricing:
                # Verify cross-reference pricing has non-zero values
                # Models with zero pricing from OpenRouter should still be filtered out
                has_valid_pricing = any(
                    is_non_zero(v)
                    for k, v in cross_ref_pricing.items()
                    if k in ("prompt", "completion")
                )
                if has_valid_pricing:
                    model_data["pricing"] = cross_ref_pricing
                    # Underscore form: model_catalog_sync guards on this exact
                    # string to skip re-normalisation. A hyphen here silently
                    # divides every cross-referenced price by the provider factor
                    # a second time.
                    model_data["pricing_source"] = "cross_reference"
                    logger.debug(
                        f"Enriched {model_id} with cross-reference pricing from OpenRouter"
                    )
                    return model_data
                else:
                    logger.debug(f"Cross-reference pricing for {model_id} is zero")

        # Gateway providers must be priced or hidden — never shown as free.
        if is_gateway_provider:
            # During catalog build, keep with zero pricing instead of filtering; the
            # background refresh prices it once cross-reference is available.
            if _is_building_catalog():
                logger.debug(f"Catalog building: keeping {model_id} with zero pricing")
                return model_data
            # WARNING, not debug: this silently removes a model from everything
            # we sell. Claude Opus 5 was invisible for two days after launch and
            # the only trace was a debug line nobody reads. record_unpriced_model
            # keeps the running set so it can be surfaced and alerted on.
            record_unpriced_model(model_id)
            logger.warning(
                "No pricing for %s — model will NOT be listed. Add pricing or it stays invisible.",
                model_id,
            )
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
    """Refresh the pricing cache by reloading from file and invalidating all derived caches."""
    global _pricing_cache, _pricing_cache_timestamp
    _pricing_cache = None
    _pricing_cache_timestamp = None
    invalidate_openrouter_pricing_index()
    return load_manual_pricing()
