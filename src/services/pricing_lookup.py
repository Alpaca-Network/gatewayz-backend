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
  - AiHubMix        -> per-1K tokens      (PricingFormat.PER_1K_TOKENS)

If you add a new pricing source, use normalize_pricing_dict() with the correct
PricingFormat constant before returning values from this module.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    """Load manual pricing data from JSON file.

    The result is cached in-memory for up to PRICING_CACHE_TTL seconds.  On the
    first access after the TTL has elapsed the cache is cleared so the next call
    reloads fresh data from disk.
    """
    global _pricing_cache, _pricing_cache_timestamp

    # Fast path: cache hit and still fresh (no lock needed for the read).
    if _pricing_cache is not None:
        age = time.monotonic() - (_pricing_cache_timestamp or 0.0)
        if age < PRICING_CACHE_TTL:
            return _pricing_cache
        # TTL expired — clear outside the lock so the locked section re-populates.
        logger.debug(
            f"Pricing cache TTL expired after {age:.0f}s (TTL={PRICING_CACHE_TTL}s); reloading"
        )
        _pricing_cache = None
        _pricing_cache_timestamp = None

    with _pricing_cache_lock:
        # Double-checked locking: re-check after acquiring the lock
        if _pricing_cache is not None:
            return _pricing_cache

        try:
            pricing_file = Path(__file__).parent.parent / "data" / "manual_pricing.json"

            if not pricing_file.exists():
                logger.warning(f"Manual pricing file not found: {pricing_file}")
                return {}

            with open(pricing_file) as f:
                raw_data = json.load(f)

            # Pre-lowercase all model keys within each gateway section to avoid
            # the O(N) case-insensitive fallback scan in get_model_pricing()
            lowercased: dict[str, Any] = {}
            for gateway_key, gateway_val in raw_data.items():
                if isinstance(gateway_val, dict):
                    lowercased[gateway_key] = {k.lower(): v for k, v in gateway_val.items()}
                else:
                    lowercased[gateway_key] = gateway_val

            _pricing_cache = lowercased
            _pricing_cache_timestamp = time.monotonic()

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
        from src.services.pricing_normalization import get_provider_format, normalize_pricing_dict

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


def get_image_pricing(provider: str, model: str) -> float | None:
    """
    Get per-image pricing from manual_pricing.json for image generation models.

    Looks up the "image_pricing" section of manual_pricing.json. Returns the
    per-image cost in USD, or None if not found (caller should fall back to
    hardcoded defaults).

    The lookup order is:
      1. Exact model match under the provider key
      2. Provider-level "default" entry
      3. None (not found)

    Args:
        provider: Image generation provider (e.g. "deepinfra", "fal", "google-vertex")
        model: Model name (e.g. "stable-diffusion-3.5-large", "flux/schnell")

    Returns:
        Cost per image in USD, or None if no config-driven pricing is available.
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

        # Try exact model match first
        model_lower = model.lower()
        entry = provider_section.get(model_lower)
        if entry is None:
            # Try original casing (keys are pre-lowercased at load time,
            # but image_pricing keys are lowercased too)
            entry = provider_section.get(model)

        # Fall back to provider-level default
        if entry is None:
            entry = provider_section.get("default")

        if entry is None:
            return None

        if isinstance(entry, dict):
            per_image = entry.get("per_image")
            if per_image is not None:
                return float(per_image)
            return None
        else:
            # Support bare numeric values for simpler entries
            return float(entry)

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


def _build_openrouter_pricing_index() -> dict[str, dict]:
    """Build an O(1) lookup index from OpenRouter models.

    Called once per catalog build cycle. Returns a dict keyed by multiple
    aliases for each model (full id, base id, lowercase variants) so that
    cross-reference lookups are O(1) instead of O(N).
    """
    global _openrouter_pricing_index
    if _openrouter_pricing_index is not None:
        return _openrouter_pricing_index

    index: dict[str, dict] = {}
    try:
        from src.services.models import get_cached_models

        openrouter_models = get_cached_models("openrouter") or []
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


def _get_cross_reference_pricing(
    model_id: str,
    openrouter_index: dict[str, dict] | None = None,
) -> dict[str, str] | None:
    """
    Get pricing for a gateway provider model by cross-referencing OpenRouter's catalog.

    Gateway providers (AiHubMix, Helicone, Anannas, Vercel) route to underlying providers
    like OpenAI, Anthropic, Google etc. This function extracts the underlying model ID
    and looks up its pricing from the OpenRouter pricing index.

    Uses an O(1) index lookup when `openrouter_index` is provided (batch path).
    Falls back to building the index on demand for single-model lookups.

    Args:
        model_id: Model ID from gateway provider (e.g., "openai/gpt-4o", "gpt-4o-mini")
        openrouter_index: Pre-built pricing index from _build_openrouter_pricing_index().
                          Pass None to have the function build/fetch the index itself.

    Returns:
        Pricing dictionary (normalized to per-token format) or None if not found
    """
    # Avoid circular dependency during catalog building
    if _is_building_catalog():
        return None

    try:
        from src.services.pricing_normalization import PricingFormat, normalize_pricing_dict

        # Use the provided index or build it on demand (single-model fallback path)
        index = (
            openrouter_index if openrouter_index is not None else _build_openrouter_pricing_index()
        )
        if not index:
            return None

        # Extract the base model name from the gateway model ID
        # e.g., "openai/gpt-4o" -> "gpt-4o", "anthropic/claude-3-opus" -> "claude-3-opus"
        base_model_id = model_id.split("/")[-1] if "/" in model_id else model_id

        # --- O(1) exact-match attempts ---
        # OpenRouter's API returns prices already in per-token format (e.g. 0.000000055),
        # so we must normalize using PER_TOKEN — not PER_1M_TOKENS.
        # This is the canonical format used everywhere in the billing pipeline.
        # See PROVIDER_PRICING_FORMATS["openrouter"] in pricing_normalization.py.
        for candidate in (model_id, model_id.lower(), base_model_id, base_model_id.lower()):
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


def _get_pricing_from_database(model_id: str) -> dict[str, str] | None:
    """
    Get pricing from database (Phase 2: database-first approach).

    Checks two sources in order:
    1. model_pricing table (JOIN) - legacy pricing storage
    2. metadata.pricing_raw - current sync storage location

    The sync service stores pricing in metadata.pricing_raw but does NOT
    populate the model_pricing table, so source #2 is the primary path.

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

        # Query models table with JOIN to model_pricing table AND metadata
        # Note: model_id column was removed - now use model_name as canonical identifier
        result = (
            client.table("models")
            .select(
                "id, model_name, metadata, model_pricing(price_per_input_token, price_per_output_token)"
            )
            .eq("model_name", model_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if not result.data or not result.data[0]:
            return None

        row = result.data[0]

        # Source 1: Try model_pricing table (legacy)
        if row.get("model_pricing"):
            pricing_data = row["model_pricing"]
            if isinstance(pricing_data, list):
                pricing_data = pricing_data[0] if pricing_data else None

            if pricing_data:
                prompt_price = pricing_data.get("price_per_input_token")
                completion_price = pricing_data.get("price_per_output_token")

                if prompt_price is not None and completion_price is not None:
                    return {
                        "prompt": validate_pricing_value(prompt_price, "prompt", model_id),
                        "completion": validate_pricing_value(
                            completion_price, "completion", model_id
                        ),
                        "request": "0",
                        "image": "0",
                    }

        # Source 2: Try metadata.pricing_raw (current sync storage)
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            pricing_raw = metadata.get("pricing_raw")
            if isinstance(pricing_raw, dict):
                prompt_price = pricing_raw.get("prompt")
                completion_price = pricing_raw.get("completion")

                if prompt_price is not None and completion_price is not None:
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
                    }

        return None

    except Exception as e:
        logger.error(f"Database pricing lookup failed for {model_id}: {e}")
        return None


def get_all_pricing_batch() -> dict[str, dict]:
    """Fetch all model pricing in a single database query.

    Returns a dict keyed by model_name with pricing sub-dicts so callers can
    do O(1) lookups instead of issuing one Supabase HTTP call per model.

    Returns:
        {model_name: {"prompt": "...", "completion": "...", "request": "...", "image": "...", "source": "..."}}
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
                    "model_name, metadata, model_pricing(price_per_input_token, price_per_output_token)"
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
            model_name = row.get("model_name")
            if not model_name:
                continue

            # Source 1: model_pricing JOIN (legacy table)
            mp = row.get("model_pricing")
            if mp and isinstance(mp, list) and len(mp) > 0:
                mp = mp[0]
            if (
                mp
                and isinstance(mp, dict)
                and (mp.get("price_per_input_token") or mp.get("price_per_output_token"))
            ):
                pricing_map[model_name] = {
                    "prompt": validate_pricing_value(
                        mp.get("price_per_input_token", 0), "prompt", model_name
                    ),
                    "completion": validate_pricing_value(
                        mp.get("price_per_output_token", 0), "completion", model_name
                    ),
                    "request": "0",
                    "image": "0",
                    "source": "database_batch",
                }
                continue

            # Source 2: metadata.pricing_raw (primary sync path)
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict):
                pricing_raw = metadata.get("pricing_raw") or metadata.get("pricing") or {}
                if isinstance(pricing_raw, dict) and (
                    pricing_raw.get("prompt") is not None
                    or pricing_raw.get("completion") is not None
                ):
                    pricing_map[model_name] = {
                        "prompt": validate_pricing_value(
                            pricing_raw.get("prompt", 0), "prompt", model_name
                        ),
                        "completion": validate_pricing_value(
                            pricing_raw.get("completion", 0), "completion", model_name
                        ),
                        "request": validate_pricing_value(
                            pricing_raw.get("request", 0), "request", model_name
                        ),
                        "image": validate_pricing_value(
                            pricing_raw.get("image", 0), "image", model_name
                        ),
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

        # For gateway providers, try cross-reference with OpenRouter
        if is_gateway_provider:
            cross_ref_pricing = _get_cross_reference_pricing(model_id, openrouter_index)
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
                    model_data["pricing_source"] = "cross-reference"
                    logger.debug(
                        f"Enriched {model_id} with cross-reference pricing from OpenRouter"
                    )
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
    """Refresh the pricing cache by reloading from file and invalidating all derived caches."""
    global _pricing_cache, _pricing_cache_timestamp
    _pricing_cache = None
    _pricing_cache_timestamp = None
    invalidate_openrouter_pricing_index()
    return load_manual_pricing()
