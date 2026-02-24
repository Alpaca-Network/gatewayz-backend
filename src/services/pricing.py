"""
Pricing Service
Handles model pricing calculations and credit cost computation
"""

import asyncio
import logging
import re
import threading
import time
from typing import Any

from src.services.model_transformations import apply_model_alias

logger = logging.getLogger(__name__)

# In-memory cache for pricing (live API + fallbacks)
# Structure: {model_id: {"data": dict, "timestamp": float}}
_pricing_cache: dict[str, dict[str, Any]] = {}
_pricing_cache_ttl = 900  # 15 minutes TTL for live pricing
_pricing_cache_lock = threading.RLock()  # Reentrant lock to prevent race conditions


# ---------------------------------------------------------------------------
# Reverse-transform lookup: maps provider-specific (transformed) model IDs
# back to their canonical/base form so pricing lookups succeed.
#
# Built lazily from model_transformations._MODEL_ID_MAPPINGS on first call.
# Key = transformed (native) model ID (lowercased)
# Value = canonical model ID that the user originally supplied (the mapping *key*
#         with an org/ prefix, i.e. the one most likely stored in the pricing DB).
# ---------------------------------------------------------------------------
_reverse_transform_cache: dict[str, str] | None = None
_reverse_transform_cache_lock = threading.Lock()

# Regex for recognised version suffixes after "@" (e.g. "@latest", "@20240620", "@1").
# Only these patterns are stripped; arbitrary text like "user@model" is left intact.
_VERSION_SUFFIX_RE = re.compile(
    r"^(?:latest|\d{8}|\d{1,10})$"  # "latest", 8-digit date (YYYYMMDD), or digit-only versions
)


def _build_reverse_transform_lookup() -> dict[str, str]:
    """
    Build a reverse lookup from provider-native model IDs back to canonical IDs.

    Uses _MODEL_ID_MAPPINGS from model_transformations.py.  For each provider
    mapping (canonical -> native), we store native -> canonical so that when a
    pricing lookup receives a transformed ID we can recover the original.

    Priority rules when multiple canonical IDs map to the same native ID:
      - Prefer entries that contain an org/ prefix (e.g. "deepseek-ai/deepseek-v3")
        over bare model names (e.g. "deepseek-v3"), because the org-prefixed form
        is what the pricing database stores.
      - Among entries with the same prefix status, the first one wins (stable).
    """
    try:
        from src.services.model_transformations import _MODEL_ID_MAPPINGS
    except ImportError:
        logger.warning("Could not import _MODEL_ID_MAPPINGS for reverse transform lookup")
        return {}

    reverse: dict[str, str] = {}

    for _provider, mapping in _MODEL_ID_MAPPINGS.items():
        for canonical, native in mapping.items():
            native_lower = native.lower()
            # Skip identity mappings (canonical == native) -- they don't help
            if canonical.lower() == native_lower:
                continue

            existing = reverse.get(native_lower)
            if existing is None:
                reverse[native_lower] = canonical
            else:
                # Prefer the entry with an org/ prefix for better DB matching
                existing_has_org = "/" in existing
                candidate_has_org = "/" in canonical
                if candidate_has_org and not existing_has_org:
                    reverse[native_lower] = canonical

    logger.info(
        "[PRICING] Built reverse-transform lookup with %d entries from %d providers",
        len(reverse),
        len(_MODEL_ID_MAPPINGS),
    )
    return reverse


def _get_reverse_transform_lookup() -> dict[str, str]:
    """Return the cached reverse-transform lookup, building it on first access.

    Uses double-checked locking to ensure thread-safe lazy initialization
    without acquiring the lock on every call after the cache is built.
    """
    global _reverse_transform_cache
    if _reverse_transform_cache is None:
        with _reverse_transform_cache_lock:
            # Double-check after acquiring the lock to avoid redundant builds
            if _reverse_transform_cache is None:
                _reverse_transform_cache = _build_reverse_transform_lookup()
    return _reverse_transform_cache


def normalize_model_id_for_pricing(model_id: str) -> str:
    """
    Normalize a potentially provider-transformed model ID back to its canonical
    form for pricing lookup.

    This solves the critical billing bug where transformed IDs (e.g.
    "accounts/fireworks/models/deepseek-v3p1") cannot be found in the pricing
    database, causing fallback to the default $0.00002/token rate.

    Normalization strategy (applied in order):
      1. Exact reverse-lookup from the _MODEL_ID_MAPPINGS table.
      2. Pattern-based stripping of known provider prefixes/suffixes:
         - Fireworks:  "accounts/fireworks/models/X" -> extract X
         - Cloudflare: "@cf/org/model" -> "org/model"
         - Google:     "@google/models/X" -> "google/X"
         - HuggingFace suffixes: ":hf-inference", ":openai", ":anthropic"
         - Onerouter version suffixes: "@latest", "@20240620", etc.
      3. Model alias resolution via apply_model_alias().

    The returned ID is suitable for cache keys and database queries.

    Args:
        model_id: The model ID that may have been transformed for a provider.

    Returns:
        The canonical/base model ID for pricing lookup.
    """
    if not model_id:
        return model_id

    original = model_id
    model_id_lower = model_id.lower()

    # ------------------------------------------------------------------
    # Step 1: Exact reverse-lookup from the transformation table.
    # This is the most reliable method -- it uses the same mappings that
    # transform_model_id() applied in the first place.
    # ------------------------------------------------------------------
    reverse_lookup = _get_reverse_transform_lookup()
    reverse_match = reverse_lookup.get(model_id_lower)
    if reverse_match:
        logger.info(
            "[PRICING_NORMALIZE] Reverse-lookup: '%s' -> '%s'",
            original,
            reverse_match,
        )
        # Apply alias resolution on the result to catch any further
        # normalization (e.g. bare model names -> org/model).
        final = apply_model_alias(reverse_match) or reverse_match
        return final

    # ------------------------------------------------------------------
    # Step 2: Pattern-based stripping of known provider prefixes/suffixes.
    # These catch cases not covered by the static mapping table.
    # ------------------------------------------------------------------
    normalized = model_id

    # 2a. Fireworks: "accounts/fireworks/models/X" -> X
    if normalized.lower().startswith("accounts/fireworks/models/"):
        stripped = normalized[len("accounts/fireworks/models/"):]
        logger.debug(
            "[PRICING_NORMALIZE] Stripped Fireworks prefix: '%s' -> '%s'",
            original,
            stripped,
        )
        normalized = stripped

    # 2b. Cloudflare Workers AI: "@cf/org/model" -> "org/model"
    elif normalized.lower().startswith("@cf/"):
        stripped = normalized[len("@cf/"):]
        logger.debug(
            "[PRICING_NORMALIZE] Stripped Cloudflare @cf/ prefix: '%s' -> '%s'",
            original,
            stripped,
        )
        normalized = stripped

    # 2c. Google Vertex: "@google/models/X" -> "google/X"
    elif normalized.lower().startswith("@google/models/"):
        stripped = "google/" + normalized[len("@google/models/"):]
        logger.debug(
            "[PRICING_NORMALIZE] Converted Google Vertex path: '%s' -> '%s'",
            original,
            stripped,
        )
        normalized = stripped

    # 2d. Clarifai: "org/app/models/model" (4-segment path) -> "org/model"
    # Clarifai model IDs look like "openai/chat-completion/models/gpt-4o"
    # or "anthropic/completion/models/claude-3-opus"
    elif "/models/" in normalized and normalized.count("/") >= 3:
        parts = normalized.split("/")
        # Pattern: org/app/models/model-name -> org/model-name
        models_idx = None
        for i, part in enumerate(parts):
            if part == "models" and i > 0 and i < len(parts) - 1:
                models_idx = i
                break
        if models_idx is not None:
            org = parts[0]
            model_name = "/".join(parts[models_idx + 1:])
            stripped = f"{org}/{model_name}"
            logger.debug(
                "[PRICING_NORMALIZE] Stripped Clarifai path: '%s' -> '%s'",
                original,
                stripped,
            )
            normalized = stripped

    # 2e. HuggingFace / provider suffixes: ":hf-inference", ":openai", ":anthropic"
    provider_suffixes = [":hf-inference", ":openai", ":anthropic"]
    for suffix in provider_suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            logger.debug(
                "[PRICING_NORMALIZE] Stripped provider suffix '%s': '%s' -> '%s'",
                suffix,
                original,
                normalized,
            )
            break

    # 2f. Onerouter / version suffixes: "@latest", "@20240620", etc.
    # IMPORTANT: Only strip if the part after @ matches known version patterns
    # to avoid incorrectly stripping "@" from model names like "user@model".
    if "@" in normalized:
        at_idx = normalized.rfind("@")
        version_part = normalized[at_idx + 1:]
        if version_part and _VERSION_SUFFIX_RE.match(version_part):
            normalized = normalized[:at_idx]
            logger.debug(
                "[PRICING_NORMALIZE] Stripped version suffix '@%s': '%s' -> '%s'",
                version_part,
                original,
                normalized,
            )

    # 2g. OpenRouter ":free" suffix (pricing-relevant -- free models handled separately
    # in calculate_cost, but if somehow we get here, strip it for lookup)
    if normalized.endswith(":free"):
        normalized = normalized[:-5]

    # ------------------------------------------------------------------
    # Step 3: Apply model alias resolution as a final pass.
    # This catches bare model names (e.g. "deepseek-r1" -> "deepseek/deepseek-r1")
    # and common typos/variants.
    # ------------------------------------------------------------------
    aliased = apply_model_alias(normalized)
    if aliased and aliased != normalized:
        logger.debug(
            "[PRICING_NORMALIZE] Applied alias: '%s' -> '%s'",
            normalized,
            aliased,
        )
        normalized = aliased

    if normalized != original:
        logger.info(
            "[PRICING_NORMALIZE] Final normalization: '%s' -> '%s'",
            original,
            normalized,
        )

    return normalized

# Track models that fall back to default pricing for monitoring/alerting
# Structure: {model_id: {"count": int, "first_seen": float, "last_seen": float, "errors": list}}
_default_pricing_tracker: dict[str, dict[str, Any]] = {}


def _track_default_pricing_usage(model_id: str, error: str | None = None) -> None:
    """
    Track when default pricing is used for a model.
    This helps identify models that need pricing data added to the database.

    Also sends Sentry alert for high-value model families (OpenAI, Anthropic, Google).
    """
    now = time.time()

    if model_id not in _default_pricing_tracker:
        _default_pricing_tracker[model_id] = {
            "count": 0,
            "first_seen": now,
            "last_seen": now,
            "errors": [],
        }

    tracker = _default_pricing_tracker[model_id]
    tracker["count"] += 1
    tracker["last_seen"] = now
    if error:
        tracker["errors"].append({"time": now, "error": error})
        # Keep only last 10 errors
        tracker["errors"] = tracker["errors"][-10:]

    # Alert for high-value model families that should have pricing
    high_value_prefixes = (
        "openai/",
        "anthropic/",
        "google/",
        "gpt-",
        "claude-",
        "gemini-",
        "o1",
        "o3",
        "o4",  # OpenAI reasoning models
    )
    model_lower = model_id.lower()

    if any(
        model_lower.startswith(prefix) or prefix in model_lower for prefix in high_value_prefixes
    ):
        # Send Sentry alert for high-value models using default pricing
        try:
            import sentry_sdk

            sentry_sdk.capture_message(
                f"High-value model using default pricing: {model_id}",
                level="warning",
                extras={
                    "model_id": model_id,
                    "usage_count": tracker["count"],
                    "first_seen": tracker["first_seen"],
                    "last_seen": tracker["last_seen"],
                    "error": error,
                },
            )
        except Exception:
            # Sentry not configured or failed, just log
            logger.error(
                f"[BILLING_ALERT] High-value model '{model_id}' using default pricing! "
                f"Usage count: {tracker['count']}. Add pricing data to prevent under-billing."
            )

    # Log Prometheus metric if available
    try:
        from src.services.prometheus_metrics import default_pricing_usage_counter

        default_pricing_usage_counter.labels(model=model_id).inc()
    except (ImportError, AttributeError):
        pass  # Prometheus metrics not available


def get_default_pricing_stats() -> dict[str, Any]:
    """Get statistics about models using default pricing for monitoring."""
    return {
        "models_using_default": len(_default_pricing_tracker),
        "details": {
            model_id: {
                "count": data["count"],
                "first_seen": data["first_seen"],
                "last_seen": data["last_seen"],
                "error_count": len(data["errors"]),
            }
            for model_id, data in _default_pricing_tracker.items()
        },
    }


def clear_pricing_cache(model_id: str | None = None) -> None:
    """Clear pricing cache (for testing or explicit invalidation).

    When a specific model_id is provided, it is normalized first so that
    both transformed and canonical IDs clear the same cache entry.
    """
    global _pricing_cache
    with _pricing_cache_lock:
        if model_id:
            # Normalize the model ID to match the cache key strategy (FIX A2)
            # Wrap in try/except so a normalization error never prevents cache clearing.
            try:
                cache_key = normalize_model_id_for_pricing(model_id)
            except Exception:
                logger.warning(
                    "Failed to normalize model ID '%s' during cache clear, "
                    "falling back to raw key",
                    model_id,
                    exc_info=True,
                )
                cache_key = model_id
            cleared = False
            # Clear both the normalized key and the raw key (belt-and-suspenders)
            for key in {cache_key, model_id}:
                if key in _pricing_cache:
                    del _pricing_cache[key]
                    cleared = True
            if cleared:
                logger.debug(
                    f"Cleared pricing cache for model {model_id} (cache_key={cache_key})"
                )
        else:
            _pricing_cache.clear()
            logger.info("Cleared entire pricing cache")


def get_pricing_cache_stats() -> dict[str, Any]:
    """Get cache statistics for monitoring"""
    return {
        "cached_models": len(_pricing_cache),
        "ttl_seconds": _pricing_cache_ttl,
    }


def _get_pricing_from_database(model_id: str, candidate_ids: set[str]) -> dict[str, float] | None:
    """
    Query database model_pricing table for pricing information via JOIN with models table.

    FIXED: Now queries the model_pricing table using the correct schema.
    - Uses model_pricing(price_per_input_token, price_per_output_token)
    - Joins via foreign key relationship
    - No longer queries deleted columns (pricing_prompt, pricing_completion)

    Args:
        model_id: Original model ID
        candidate_ids: Set of model ID variations to try

    Returns:
        Pricing dict with prompt/completion prices (per token), or None if not found
    """
    try:
        from src.config.supabase_config import get_supabase_client
        from src.services.prometheus_metrics import track_database_query

        client = get_supabase_client()

        # Try querying with each candidate ID
        for candidate in candidate_ids:
            if not candidate:
                continue

            try:
                with track_database_query(table="models", operation="select"):
                    # Query models table with JOIN to model_pricing table
                    # PostgREST syntax: select model_pricing(*) creates a nested object
                    # Note: model_id column was removed - now use model_name as canonical identifier
                    result = (
                        client.table("models")
                        .select(
                            "id, model_name, model_pricing(price_per_input_token, price_per_output_token)"
                        )
                        .eq("model_name", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]

                    # Check if model_pricing relationship exists
                    if not row.get("model_pricing"):
                        logger.debug(f"[DB] Model {candidate} found but has no pricing entry")
                        continue

                    pricing_data = row["model_pricing"]

                    # Handle case where model_pricing is a list (PostgREST may return array for one-to-many)
                    if isinstance(pricing_data, list):
                        if not pricing_data:
                            logger.debug(f"[DB] Model {candidate} has empty pricing array")
                            continue
                        pricing_data = pricing_data[0]  # Take first pricing entry

                    # Extract pricing values (these are per-token prices)
                    prompt_price = pricing_data.get("price_per_input_token")
                    completion_price = pricing_data.get("price_per_output_token")

                    if prompt_price is None or completion_price is None:
                        logger.debug(f"[DB] Model {candidate} has incomplete pricing data")
                        continue

                    # Convert to float and ensure non-negative values
                    prompt_price = max(0.0, float(prompt_price))
                    completion_price = max(0.0, float(completion_price))

                    logger.info(
                        f"[DB SUCCESS] Found pricing for {model_id} (matched: {candidate}): "
                        f"prompt=${prompt_price}/token, completion=${completion_price}/token"
                    )

                    return {
                        "prompt": prompt_price,
                        "completion": completion_price,
                        "found": True,
                        "source": "database",
                    }

                # Try provider_model_id if model_name didn't match
                with track_database_query(table="models", operation="select"):
                    result = (
                        client.table("models")
                        .select(
                            "id, model_name, model_pricing(price_per_input_token, price_per_output_token)"
                        )
                        .eq("provider_model_id", candidate)
                        .eq("is_active", True)
                        .limit(1)
                        .execute()
                    )

                if result.data and len(result.data) > 0:
                    row = result.data[0]

                    # Check if model_pricing relationship exists
                    if not row.get("model_pricing"):
                        logger.debug(
                            f"[DB] Model {candidate} (provider_model_id) found but has no pricing entry"
                        )
                        continue

                    pricing_data = row["model_pricing"]

                    # Handle list case
                    if isinstance(pricing_data, list):
                        if not pricing_data:
                            continue
                        pricing_data = pricing_data[0]

                    # Extract pricing values
                    prompt_price = pricing_data.get("price_per_input_token")
                    completion_price = pricing_data.get("price_per_output_token")

                    if prompt_price is None or completion_price is None:
                        continue

                    # Convert to float and ensure non-negative values
                    prompt_price = max(0.0, float(prompt_price))
                    completion_price = max(0.0, float(completion_price))

                    logger.info(
                        f"[DB SUCCESS] Found pricing for {model_id} (matched provider_model_id: {candidate}): "
                        f"prompt=${prompt_price}/token, completion=${completion_price}/token"
                    )

                    return {
                        "prompt": prompt_price,
                        "completion": completion_price,
                        "found": True,
                        "source": "database",
                    }

            except Exception as e:
                logger.debug(f"Database query failed for candidate {candidate}: {e}")
                continue

        # No match found in database
        logger.debug(
            f"[DB MISS] No pricing found in database for {model_id} (tried {len(candidate_ids)} candidates)"
        )
        return None

    except Exception as e:
        logger.warning(f"Database pricing lookup error for {model_id}: {e}")
        return None


def _get_pricing_from_cache_fallback(
    model_id: str, candidate_ids: set[str]
) -> dict[str, float] | None:
    """
    Fallback to in-memory cache from provider APIs (old behavior).

    Args:
        model_id: Original model ID
        candidate_ids: Set of model ID variations to try

    Returns:
        Pricing dict with prompt/completion prices, or None if not found
    """
    try:
        from src.services.models import get_cached_models

        models = get_cached_models("all")
        if not models:
            return None

        # Find the specific model - try both original and normalized IDs
        for model in models:
            model_catalog_id = model.get("id")
            model_slug = model.get("slug")

            # Match against candidate IDs
            if (model_catalog_id and model_catalog_id in candidate_ids) or (
                model_slug and model_slug in candidate_ids
            ):
                pricing = model.get("pricing", {})

                # Handle case where pricing might be None instead of dict
                if not isinstance(pricing, dict):
                    pricing = {}

                # Convert pricing strings to floats, handling None and empty strings
                prompt_price = float(pricing.get("prompt", "0") or "0")
                prompt_price = max(0.0, prompt_price)

                completion_price = float(pricing.get("completion", "0") or "0")
                completion_price = max(0.0, completion_price)

                logger.info(
                    f"[CACHE FALLBACK] Found pricing for {model_id}: "
                    f"prompt=${prompt_price}, completion=${completion_price}"
                )

                return {
                    "prompt": prompt_price,
                    "completion": completion_price,
                    "found": True,
                    "source": "cache_fallback",
                }

        return None

    except Exception as e:
        logger.warning(f"Cache fallback pricing lookup error for {model_id}: {e}")
        return None


def get_model_pricing(model_id: str) -> dict[str, float]:
    """
    Get pricing information for a specific model.

    Pricing lookup priority (UPDATED - PHASE 0):
    1. In-memory cache (15min TTL) -- keyed by NORMALIZED model ID
    2. Live API fetch from provider (DEPRECATED)
    3. Database (model_pricing table) - PHASE 0 FIX
    4. Provider API cache / JSON fallback (manual_pricing.json)
    5. Default pricing ($0.00002 per token)

    IMPORTANT: Before any lookup, the model ID is normalized via
    normalize_model_id_for_pricing() to reverse provider-specific transformations
    (e.g. "accounts/fireworks/models/deepseek-v3p1" -> "deepseek-ai/deepseek-v3").
    This ensures the pricing DB/cache lookup matches the canonical model ID even
    when the caller passes a transformed (provider-native) model ID.

    Args:
        model_id: The model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus",
                  or a transformed ID like "accounts/fireworks/models/deepseek-v3p1")

    Returns:
        Dictionary with pricing info:
        {
            "prompt": float,      # Cost per prompt token in USD
            "completion": float,  # Cost per completion token in USD
            "found": bool,        # Whether the model was found
            "source": str         # Source of pricing: live_api_*|cache_fallback|default
        }
    """
    try:
        # Import here to avoid circular imports
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return default pricing to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Returning default pricing for {model_id} (catalog building in progress)")
            return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}

        # ---------------------------------------------------------------
        # FIX A1: Normalize the model ID BEFORE any cache/DB lookup.
        # This reverses provider-specific transformations so that e.g.
        # "accounts/fireworks/models/deepseek-v3p1" is looked up as
        # "deepseek-ai/deepseek-v3", which is what the pricing DB stores.
        # ---------------------------------------------------------------
        normalized_model_id = normalize_model_id_for_pricing(model_id)

        # Build candidate IDs for lookup (original + normalized variations)
        candidate_ids = {model_id, normalized_model_id}

        # Also add the alias-resolved form if different
        aliased_model_id = apply_model_alias(normalized_model_id)
        if aliased_model_id and aliased_model_id != normalized_model_id:
            logger.info(
                "Resolved pricing lookup alias: '%s' -> '%s'",
                normalized_model_id,
                aliased_model_id,
            )
            candidate_ids.add(aliased_model_id)

        # Strip provider-specific suffixes as additional candidates
        # (kept for backward compat, though normalize_model_id_for_pricing
        #  already handles these)
        for suffix in [":hf-inference", ":openai", ":anthropic"]:
            if model_id.endswith(suffix):
                candidate_ids.add(model_id[: -len(suffix)])
                break

        # Remove any empty candidates that might have been added
        candidate_ids = {cid for cid in candidate_ids if cid}

        # ---------------------------------------------------------------
        # FIX A2: Use the NORMALIZED model ID as cache key.
        # This ensures that both "accounts/fireworks/models/deepseek-v3p1"
        # and "deepseek-ai/deepseek-v3" hit the same cache entry, preventing
        # redundant lookups and ensuring consistent pricing.
        # ---------------------------------------------------------------
        cache_key = normalized_model_id

        # Step 1: Check in-memory cache (fastest) - with thread safety
        with _pricing_cache_lock:
            cache_entry = _pricing_cache.get(cache_key)
            if cache_entry:
                age = time.time() - cache_entry["timestamp"]
                if age < _pricing_cache_ttl:
                    logger.debug(
                        f"[CACHE HIT] Pricing for {model_id} "
                        f"(cache_key={cache_key}, age: {age:.1f}s)"
                    )
                    return cache_entry["data"]
                else:
                    # Cache expired, remove it
                    del _pricing_cache[cache_key]
                    logger.debug(
                        f"[CACHE EXPIRED] Pricing for {model_id} "
                        f"(cache_key={cache_key}, age: {age:.1f}s)"
                    )

        # Step 2: Live API fetch - DEPRECATED (Phase 2)
        # NOTE: pricing_live_fetch module was removed as part of pricing sync deprecation.
        # Live pricing fetching is no longer supported. All pricing now comes from:
        # 1. Database (models_catalog table via model sync)
        # 2. Manual pricing file (manual_pricing.json)
        # 3. Default pricing (fallback)
        logger.debug(
            f"[PRICING] Live API fetch is deprecated. Using database/manual pricing for {model_id}"
        )

        # Step 3: Try database pricing (PHASE 0 FIX)
        try:
            db_pricing = _get_pricing_from_database(model_id, candidate_ids)
            if db_pricing:
                # Cache the database result using NORMALIZED key - with thread safety
                with _pricing_cache_lock:
                    _pricing_cache[cache_key] = {"data": db_pricing, "timestamp": time.time()}
                logger.info(
                    f"[DB FALLBACK] Using database pricing for {model_id} "
                    f"(cache_key={cache_key})"
                )
                return db_pricing
        except Exception as e:
            logger.warning(f"Database pricing lookup failed for {model_id}: {e}")

        # Step 4: Fallback to provider API cache (for resilience)
        try:
            cache_pricing = _get_pricing_from_cache_fallback(model_id, candidate_ids)
            if cache_pricing:
                # Cache the fallback result using NORMALIZED key - with thread safety
                with _pricing_cache_lock:
                    _pricing_cache[cache_key] = {"data": cache_pricing, "timestamp": time.time()}
                return cache_pricing
        except Exception as e:
            logger.warning(f"Cache fallback pricing lookup failed for {model_id}: {e}")

        # Step 5: Use default pricing (last resort)
        # ALERT: Default pricing may significantly under-bill for expensive models

        # HIGH-VALUE MODEL CHECK: Block requests for expensive models with unknown pricing
        # This prevents massive revenue loss from using default pricing on GPT-4, Claude, etc.
        # NOTE: Check against BOTH original and normalized IDs to catch transformed variants
        HIGH_VALUE_MODEL_PATTERNS = [
            "gpt-4",
            "gpt-5",
            "o1-",
            "o3-",
            "o4-",  # OpenAI high-end models
            "claude-3",
            "claude-opus",
            "claude-sonnet-4",  # Anthropic high-end
            "gemini-1.5-pro",
            "gemini-2",
            "gemini-pro",  # Google high-end
            "command-r-plus",  # Cohere high-end
            "mixtral-8x22b",  # Mistral high-end
        ]

        # Check both original and normalized IDs against high-value patterns
        # (the normalized ID may reveal the true model identity from a transformed ID)
        model_id_lower = model_id.lower()
        normalized_lower = normalized_model_id.lower()
        is_high_value = any(
            pattern in model_id_lower or pattern in normalized_lower
            for pattern in HIGH_VALUE_MODEL_PATTERNS
        )

        if is_high_value:
            error_msg = (
                f"HIGH_VALUE_MODEL_PRICING_MISSING: Cannot use default pricing for {model_id}. "
                f"Normalized as: {normalized_model_id}. "
                f"This model requires accurate pricing data to prevent significant under-billing. "
                f"Please contact support to add pricing for this model."
            )
            logger.error(error_msg)

            # Send critical Sentry alert
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    error_msg,
                    level="error",
                    extras={
                        "model_id": model_id,
                        "normalized_model_id": normalized_model_id,
                        "candidate_ids": list(candidate_ids),
                        "default_pricing_would_be": 0.00002,
                    },
                )
            except Exception:
                pass

            # Block the request to prevent revenue loss
            raise ValueError(
                f"Pricing data not available for model '{model_id}'. "
                f"This model cannot be used until pricing is configured. "
                f"Please try a different model or contact support."
            )

        # For non-high-value models, allow default pricing but track usage
        logger.warning(
            f"[DEFAULT_PRICING_ALERT] Model {model_id} (normalized: {normalized_model_id}) "
            f"not found in database or cache, "
            f"using default pricing ($0.00002/token). This may under-bill for expensive models."
        )
        _track_default_pricing_usage(model_id)
        default_pricing = {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default",
        }
        return default_pricing

    except Exception as e:
        logger.error(f"Error getting pricing for model {model_id}: {e}", exc_info=True)
        _track_default_pricing_usage(model_id, error=str(e))
        return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}


async def get_model_pricing_async(model_id: str) -> dict[str, float]:
    """
    Get pricing information for a specific model (async version).

    This is the preferred method when called from async contexts (route handlers, etc.).
    Uses the same caching strategy as get_model_pricing but handles async operations natively.

    Pricing lookup priority (UPDATED - PHASE 0):
    1. In-memory cache (15min TTL) -- keyed by NORMALIZED model ID
    2. Live API fetch from provider (DEPRECATED)
    3. Database (model_pricing table) - PHASE 0 FIX
    4. Provider API cache / JSON fallback (manual_pricing.json)
    5. Default pricing ($0.00002 per token)

    IMPORTANT: Before any lookup, the model ID is normalized via
    normalize_model_id_for_pricing() to reverse provider-specific transformations.

    Args:
        model_id: The model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus",
                  or a transformed ID like "accounts/fireworks/models/deepseek-v3p1")

    Returns:
        Dictionary with pricing info
    """
    try:
        # Import here to avoid circular imports
        from src.services.models import _is_building_catalog

        # If we're building the catalog, return default pricing to avoid circular dependency
        if _is_building_catalog():
            logger.debug(f"Returning default pricing for {model_id} (catalog building in progress)")
            return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}

        # ---------------------------------------------------------------
        # FIX A1: Normalize the model ID BEFORE any cache/DB lookup.
        # This reverses provider-specific transformations so that e.g.
        # "accounts/fireworks/models/deepseek-v3p1" is looked up as
        # "deepseek-ai/deepseek-v3", which is what the pricing DB stores.
        # ---------------------------------------------------------------
        normalized_model_id = normalize_model_id_for_pricing(model_id)

        # Build candidate IDs for lookup (original + normalized variations)
        candidate_ids = {model_id, normalized_model_id}

        # Also add the alias-resolved form if different
        aliased_model_id = apply_model_alias(normalized_model_id)
        if aliased_model_id and aliased_model_id != normalized_model_id:
            logger.info(
                "Resolved pricing lookup alias: '%s' -> '%s'",
                normalized_model_id,
                aliased_model_id,
            )
            candidate_ids.add(aliased_model_id)

        # Strip provider-specific suffixes as additional candidates
        for suffix in [":hf-inference", ":openai", ":anthropic"]:
            if model_id.endswith(suffix):
                candidate_ids.add(model_id[: -len(suffix)])
                break

        # Remove any empty candidates
        candidate_ids = {cid for cid in candidate_ids if cid}

        # ---------------------------------------------------------------
        # FIX A2: Use the NORMALIZED model ID as cache key.
        # This ensures that both "accounts/fireworks/models/deepseek-v3p1"
        # and "deepseek-ai/deepseek-v3" hit the same cache entry.
        # ---------------------------------------------------------------
        cache_key = normalized_model_id

        # Step 1: Check in-memory cache (fastest) - with thread safety
        with _pricing_cache_lock:
            cache_entry = _pricing_cache.get(cache_key)
            if cache_entry:
                age = time.time() - cache_entry["timestamp"]
                if age < _pricing_cache_ttl:
                    logger.debug(
                        f"[CACHE HIT] Pricing for {model_id} "
                        f"(cache_key={cache_key}, age: {age:.1f}s)"
                    )
                    return cache_entry["data"]
                else:
                    # Cache expired, remove it
                    del _pricing_cache[cache_key]
                    logger.debug(
                        f"[CACHE EXPIRED] Pricing for {model_id} "
                        f"(cache_key={cache_key}, age: {age:.1f}s)"
                    )

        # Step 2: Live API fetch - DEPRECATED (Phase 2)
        # NOTE: pricing_live_fetch module was removed as part of pricing sync deprecation.
        # Live pricing fetching is no longer supported. All pricing now comes from:
        # 1. Database (models_catalog table via model sync)
        # 2. Manual pricing file (manual_pricing.json)
        # 3. Default pricing (fallback)
        logger.debug(
            f"[PRICING] Live API fetch is deprecated. Using database/manual pricing for {model_id}"
        )

        # Step 3: Try database pricing (PHASE 0 FIX)
        try:
            db_pricing = await asyncio.to_thread(
                _get_pricing_from_database, model_id, candidate_ids
            )
            if db_pricing:
                # Cache the database result using NORMALIZED key - with thread safety
                with _pricing_cache_lock:
                    _pricing_cache[cache_key] = {"data": db_pricing, "timestamp": time.time()}
                logger.info(
                    f"[DB FALLBACK] Using database pricing for {model_id} "
                    f"(cache_key={cache_key})"
                )
                return db_pricing
        except Exception as e:
            logger.warning(f"Database pricing lookup failed for {model_id}: {e}")

        # Step 4: Fallback to provider API cache (for resilience)
        try:
            cache_pricing = await asyncio.to_thread(
                _get_pricing_from_cache_fallback, model_id, candidate_ids
            )
            if cache_pricing:
                # Cache the fallback result using NORMALIZED key
                with _pricing_cache_lock:
                    _pricing_cache[cache_key] = {"data": cache_pricing, "timestamp": time.time()}
                return cache_pricing
        except Exception as e:
            logger.warning(f"Cache fallback pricing lookup failed for {model_id}: {e}")

        # Step 5: Use default pricing (last resort)
        # ALERT: Default pricing may significantly under-bill for expensive models

        # HIGH-VALUE MODEL CHECK: Block requests for expensive models with unknown pricing
        # This prevents massive revenue loss from using default pricing on GPT-4, Claude, etc.
        # NOTE: Check against BOTH original and normalized IDs to catch transformed variants
        HIGH_VALUE_MODEL_PATTERNS = [
            "gpt-4",
            "gpt-5",
            "o1-",
            "o3-",
            "o4-",  # OpenAI high-end models
            "claude-3",
            "claude-opus",
            "claude-sonnet-4",  # Anthropic high-end
            "gemini-1.5-pro",
            "gemini-2",
            "gemini-pro",  # Google high-end
            "command-r-plus",  # Cohere high-end
            "mixtral-8x22b",  # Mistral high-end
        ]

        # Check both original and normalized IDs against high-value patterns
        model_id_lower = model_id.lower()
        normalized_lower = normalized_model_id.lower()
        is_high_value = any(
            pattern in model_id_lower or pattern in normalized_lower
            for pattern in HIGH_VALUE_MODEL_PATTERNS
        )

        if is_high_value:
            error_msg = (
                f"HIGH_VALUE_MODEL_PRICING_MISSING: Cannot use default pricing for {model_id}. "
                f"Normalized as: {normalized_model_id}. "
                f"This model requires accurate pricing data to prevent significant under-billing. "
                f"Please contact support to add pricing for this model."
            )
            logger.error(error_msg)

            # Send critical Sentry alert
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    error_msg,
                    level="error",
                    extras={
                        "model_id": model_id,
                        "normalized_model_id": normalized_model_id,
                        "candidate_ids": list(candidate_ids),
                        "default_pricing_would_be": 0.00002,
                    },
                )
            except Exception:
                pass

            # Block the request to prevent revenue loss
            raise ValueError(
                f"Pricing data not available for model '{model_id}'. "
                f"This model cannot be used until pricing is configured. "
                f"Please try a different model or contact support."
            )

        # For non-high-value models, allow default pricing but track usage
        logger.warning(
            f"[DEFAULT_PRICING_ALERT] Model {model_id} (normalized: {normalized_model_id}) "
            f"not found via live API, database, or cache, "
            f"using default pricing ($0.00002/token). This may under-bill for expensive models."
        )
        _track_default_pricing_usage(model_id)
        default_pricing = {
            "prompt": 0.00002,
            "completion": 0.00002,
            "found": False,
            "source": "default",
        }
        return default_pricing

    except ValueError:
        # Re-raise validation errors (high-value model pricing missing)
        raise
    except Exception as e:
        logger.error(f"Error getting pricing for model {model_id}: {e}", exc_info=True)
        _track_default_pricing_usage(model_id, error=str(e))

        # Check if this is a high-value model even in error case
        HIGH_VALUE_MODEL_PATTERNS = [
            "gpt-4",
            "gpt-5",
            "o1-",
            "o3-",
            "o4-",
            "claude-3",
            "claude-opus",
            "claude-sonnet-4",
            "gemini-1.5-pro",
            "gemini-2",
            "gemini-pro",
            "command-r-plus",
            "mixtral-8x22b",
        ]
        if any(pattern in model_id.lower() for pattern in HIGH_VALUE_MODEL_PATTERNS):
            raise ValueError(
                f"Pricing data not available for high-value model '{model_id}'. "
                f"Request blocked to prevent under-billing."
            )

        return {"prompt": 0.00002, "completion": 0.00002, "found": False, "source": "default"}


def calculate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the total cost for a chat completion based on model pricing

    Args:
        model_id: The model ID
        prompt_tokens: Number of prompt tokens used
        completion_tokens: Number of completion tokens used

    Returns:
        Total cost in USD

    Raises:
        ValueError: If pricing is invalid or outside acceptable bounds
    """
    try:
        # Check if this is a free model first (OpenRouter free models end with :free)
        # VALIDATION: Only OpenRouter models should have :free suffix to prevent abuse
        if model_id and model_id.endswith(":free"):
            # Validate that this is actually from OpenRouter
            # OpenRouter model IDs typically start with provider name (e.g., "openai/gpt-4:free")
            # or are just model names for OpenRouter-exclusive models
            is_openrouter_model = (
                "/" in model_id  # Has provider prefix (OpenRouter format)
                or not any(
                    provider in model_id.lower()
                    for provider in ["anthropic", "google", "cohere", "mistral", "deepseek"]
                )  # Not obviously from another provider
            )

            if not is_openrouter_model:
                # Suspicious :free suffix on non-OpenRouter model
                logger.warning(
                    f"PRICING_VALIDATION: Model {model_id} has :free suffix but doesn't appear to be from OpenRouter. "
                    f"Removing suffix and charging normally to prevent abuse."
                )
                # Strip the :free suffix and continue with normal pricing
                model_id = model_id[:-5]  # Remove ":free"
            else:
                logger.info(f"Free model detected: {model_id}, returning $0 cost")
                return 0.0

        pricing = get_model_pricing(model_id)

        # FIXED: Pricing is per single token, so just multiply (no division)
        prompt_cost = prompt_tokens * pricing["prompt"]
        completion_cost = completion_tokens * pricing["completion"]
        total_cost = prompt_cost + completion_cost

        # PRICING SANITY CHECK: Validate cost is within reasonable bounds
        # This catches pricing normalization errors (1000x bugs) and missing pricing
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens > 0:
            cost_per_1k_tokens = (total_cost / total_tokens) * 1000

            # Reasonable bounds: $0.0001 to $100 per 1K tokens
            # - Lower bound catches missing/zero pricing
            # - Upper bound catches 1000x normalization errors
            MIN_COST_PER_1K = 0.0001  # $0.0001 per 1K tokens (very cheap models)
            MAX_COST_PER_1K = 100.0  # $100 per 1K tokens (extremely expensive models)

            if cost_per_1k_tokens < MIN_COST_PER_1K:
                # Suspiciously low pricing - likely missing or zero pricing
                error_msg = (
                    f"PRICING_ANOMALY: {model_id} cost ${cost_per_1k_tokens:.8f} per 1K tokens "
                    f"is below minimum ${MIN_COST_PER_1K}. This likely indicates missing pricing data. "
                    f"Pricing source: {pricing.get('source', 'unknown')}"
                )
                logger.error(error_msg)

                # If this is default pricing being used, raise error to prevent under-billing
                if pricing.get("source") == "default":
                    raise ValueError(
                        f"Cannot use default pricing for model {model_id}. "
                        f"Actual pricing data required to prevent under-billing."
                    )

            elif cost_per_1k_tokens > MAX_COST_PER_1K:
                # Suspiciously high pricing - likely normalization error (1000x bug)
                error_msg = (
                    f"PRICING_ANOMALY: {model_id} cost ${cost_per_1k_tokens:.2f} per 1K tokens "
                    f"exceeds maximum ${MAX_COST_PER_1K}. This likely indicates a pricing normalization error. "
                    f"Raw pricing: prompt=${pricing['prompt']}, completion=${pricing['completion']}, "
                    f"source: {pricing.get('source', 'unknown')}"
                )
                logger.error(error_msg)

                # Send alert for manual review
                try:
                    import sentry_sdk

                    sentry_sdk.capture_message(
                        error_msg,
                        level="error",
                        extras={
                            "model_id": model_id,
                            "cost_per_1k_tokens": cost_per_1k_tokens,
                            "total_cost": total_cost,
                            "total_tokens": total_tokens,
                            "pricing": pricing,
                        },
                    )
                except Exception:
                    pass

                raise ValueError(
                    f"Pricing anomaly detected for {model_id}: ${cost_per_1k_tokens:.2f} per 1K tokens. "
                    f"Request blocked to prevent overcharging. Please contact support."
                )

        logger.info(
            f"Cost calculation for {model_id}: "
            f"{prompt_tokens} prompt tokens (${prompt_cost:.6f}) + "
            f"{completion_tokens} completion tokens (${completion_cost:.6f}) = "
            f"${total_cost:.6f}"
        )

        return total_cost

    except ValueError:
        # Re-raise validation errors without modification
        raise
    except Exception as e:
        logger.error(f"Error calculating cost for {model_id}: {e}")
        # Fallback: Check if free model before applying default pricing
        if model_id and model_id.endswith(":free"):
            logger.info(f"Free model detected in fallback: {model_id}, returning $0 cost")
            return 0.0
        # Fallback to simple calculation (assuming $0.00002 per token)
        total_tokens = prompt_tokens + completion_tokens
        return total_tokens * 0.00002


async def calculate_cost_async(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the total cost for a chat completion based on model pricing (async version).

    This version uses get_model_pricing_async() which can fetch live pricing from
    provider APIs when called from async contexts.

    Args:
        model_id: The model ID
        prompt_tokens: Number of prompt tokens used
        completion_tokens: Number of completion tokens used

    Returns:
        Total cost in USD

    Raises:
        ValueError: If pricing is invalid or outside acceptable bounds
    """
    try:
        # Check if this is a free model first (OpenRouter free models end with :free)
        # VALIDATION: Only OpenRouter models should have :free suffix to prevent abuse
        if model_id and model_id.endswith(":free"):
            # Validate that this is actually from OpenRouter
            # OpenRouter model IDs typically start with provider name (e.g., "openai/gpt-4:free")
            # or are just model names for OpenRouter-exclusive models
            is_openrouter_model = (
                "/" in model_id  # Has provider prefix (OpenRouter format)
                or not any(
                    provider in model_id.lower()
                    for provider in ["anthropic", "google", "cohere", "mistral", "deepseek"]
                )  # Not obviously from another provider
            )

            if not is_openrouter_model:
                # Suspicious :free suffix on non-OpenRouter model
                logger.warning(
                    f"PRICING_VALIDATION: Model {model_id} has :free suffix but doesn't appear to be from OpenRouter. "
                    f"Removing suffix and charging normally to prevent abuse."
                )
                # Strip the :free suffix and continue with normal pricing
                model_id = model_id[:-5]  # Remove ":free"
            else:
                logger.info(f"Free model detected: {model_id}, returning $0 cost")
                return 0.0

        pricing = await get_model_pricing_async(model_id)

        # Pricing is per single token, so just multiply (no division)
        prompt_cost = prompt_tokens * pricing["prompt"]
        completion_cost = completion_tokens * pricing["completion"]
        total_cost = prompt_cost + completion_cost

        # PRICING SANITY CHECK: Validate cost is within reasonable bounds
        # This catches pricing normalization errors (1000x bugs) and missing pricing
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens > 0:
            cost_per_1k_tokens = (total_cost / total_tokens) * 1000

            # Reasonable bounds: $0.0001 to $100 per 1K tokens
            # - Lower bound catches missing/zero pricing
            # - Upper bound catches 1000x normalization errors
            MIN_COST_PER_1K = 0.0001  # $0.0001 per 1K tokens (very cheap models)
            MAX_COST_PER_1K = 100.0  # $100 per 1K tokens (extremely expensive models)

            if cost_per_1k_tokens < MIN_COST_PER_1K:
                # Suspiciously low pricing - likely missing or zero pricing
                error_msg = (
                    f"PRICING_ANOMALY: {model_id} cost ${cost_per_1k_tokens:.8f} per 1K tokens "
                    f"is below minimum ${MIN_COST_PER_1K}. This likely indicates missing pricing data. "
                    f"Pricing source: {pricing.get('source', 'unknown')}"
                )
                logger.error(error_msg)

                # If this is default pricing being used, raise error to prevent under-billing
                if pricing.get("source") == "default":
                    raise ValueError(
                        f"Cannot use default pricing for model {model_id}. "
                        f"Actual pricing data required to prevent under-billing."
                    )

            elif cost_per_1k_tokens > MAX_COST_PER_1K:
                # Suspiciously high pricing - likely normalization error (1000x bug)
                error_msg = (
                    f"PRICING_ANOMALY: {model_id} cost ${cost_per_1k_tokens:.2f} per 1K tokens "
                    f"exceeds maximum ${MAX_COST_PER_1K}. This likely indicates a pricing normalization error. "
                    f"Raw pricing: prompt=${pricing['prompt']}, completion=${pricing['completion']}, "
                    f"source: {pricing.get('source', 'unknown')}"
                )
                logger.error(error_msg)

                # Send alert for manual review
                try:
                    import sentry_sdk

                    sentry_sdk.capture_message(
                        error_msg,
                        level="error",
                        extras={
                            "model_id": model_id,
                            "cost_per_1k_tokens": cost_per_1k_tokens,
                            "total_cost": total_cost,
                            "total_tokens": total_tokens,
                            "pricing": pricing,
                        },
                    )
                except Exception:
                    pass

                raise ValueError(
                    f"Pricing anomaly detected for {model_id}: ${cost_per_1k_tokens:.2f} per 1K tokens. "
                    f"Request blocked to prevent overcharging. Please contact support."
                )

        logger.info(
            f"Cost calculation (async) for {model_id}: "
            f"{prompt_tokens} prompt tokens (${prompt_cost:.6f}) + "
            f"{completion_tokens} completion tokens (${completion_cost:.6f}) = "
            f"${total_cost:.6f}"
        )

        return total_cost

    except ValueError:
        # Re-raise validation errors without modification
        raise
    except Exception as e:
        logger.error(f"Error calculating cost for {model_id}: {e}")
        # Fallback: Check if free model before applying default pricing
        if model_id and model_id.endswith(":free"):
            logger.info(f"Free model detected in fallback: {model_id}, returning $0 cost")
            return 0.0
        # Fallback to simple calculation (assuming $0.00002 per token)
        total_tokens = prompt_tokens + completion_tokens
        return total_tokens * 0.00002


# ==================== Code Router Savings Calculation ====================


def calculate_code_router_savings(
    selected_model_id: str,
    actual_cost: float,
    input_tokens: int,
    output_tokens: int,
    baselines: dict[str, dict[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """
    Calculate savings from code router vs baseline models.

    Args:
        selected_model_id: The model selected by the code router
        actual_cost: The actual cost in USD
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        baselines: Optional baseline configuration. If not provided, uses default baselines.

    Returns:
        Dict with savings per baseline:
        {
            "opus_4_5": {"baseline_cost": 0.05, "actual_cost": 0.001, "savings": 0.049, "percent": 98.0},
            "gpt_5_2": {...},
            "user_default": {...}
        }
    """
    if baselines is None:
        # Load baselines from code quality priors if available, else use defaults
        # This ensures pricing matches the router configuration
        try:
            from src.services.code_router import get_baselines as get_router_baselines

            router_baselines = get_router_baselines()
            baselines = {
                name: {
                    "price_input": config.get("price_input", 3.0),
                    "price_output": config.get("price_output", 15.0),
                }
                for name, config in router_baselines.items()
            }
        except (ImportError, Exception):
            # Fallback to default baselines (prices per million tokens)
            baselines = {
                "claude_3_5_sonnet": {"price_input": 3.0, "price_output": 15.0},
                "gpt_4o": {"price_input": 2.50, "price_output": 10.0},
            }

    savings: dict[str, dict[str, float]] = {}

    for baseline_name, baseline_prices in baselines.items():
        # Calculate baseline cost (prices are per million tokens)
        baseline_input_cost = (input_tokens / 1_000_000) * baseline_prices.get("price_input", 0)
        baseline_output_cost = (output_tokens / 1_000_000) * baseline_prices.get("price_output", 0)
        baseline_cost = baseline_input_cost + baseline_output_cost

        # Calculate savings
        raw_savings = max(0, baseline_cost - actual_cost)
        percent_savings = (raw_savings / baseline_cost * 100) if baseline_cost > 0 else 0

        savings[baseline_name] = {
            "baseline_cost_usd": round(baseline_cost, 6),
            "actual_cost_usd": round(actual_cost, 6),
            "savings_usd": round(raw_savings, 6),
            "savings_percent": round(percent_savings, 1),
        }

        logger.debug(
            f"Code router savings vs {baseline_name}: "
            f"${raw_savings:.6f} ({percent_savings:.1f}%)"
        )

    return savings


def track_code_router_cost_metrics(
    selected_model_id: str,
    actual_cost: float,
    input_tokens: int,
    output_tokens: int,
    task_category: str,
) -> None:
    """
    Track cost-related metrics for code router in Prometheus.

    Args:
        selected_model_id: The model selected by the code router
        actual_cost: The actual cost in USD
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        task_category: The classified task category
    """
    try:
        from src.services.prometheus_metrics import track_code_router_savings

        # Calculate savings vs baselines
        savings = calculate_code_router_savings(
            selected_model_id=selected_model_id,
            actual_cost=actual_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        # Track each baseline savings
        for baseline_name, savings_data in savings.items():
            track_code_router_savings(
                baseline=baseline_name,
                task_category=task_category,
                savings_usd=savings_data["savings_usd"],
            )

    except ImportError:
        logger.debug("Prometheus metrics not available for code router cost tracking")
    except Exception as e:
        logger.debug(f"Failed to track code router cost metrics: {e}")
