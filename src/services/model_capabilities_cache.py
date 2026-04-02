"""
In-memory cache for model capability flags and quality scores.

Loads the models table capability columns and model_quality_scores from the
database at startup and caches them in process memory. Automatically refreshes
every 15 minutes on next access (stale-while-revalidate — never blocks a request).

This replaces the following hardcoded Python structures:
  - MODEL_MAX_TOKENS in credit_precheck.py
  - ANONYMOUS_ALLOWED_MODELS in anonymous_rate_limiter.py
  - QUALITY_PRIORS in model_selector.py
  - SMALL_TIER_POOL / MEDIUM_TIER_POOL in health_snapshots.py
  - STABLE_FALLBACK_MODELS in prompt_router.py
  - ULTRA_LOW_LATENCY_MODELS in request_prioritization.py

Usage:
    # At startup (called from staggered_db_warmup in startup.py):
    load_model_capabilities_cache()

    # In services:
    from src.services.model_capabilities_cache import (
        get_max_output_tokens,
        get_quality_priors,
        get_free_models,
        is_free_model,
        get_models_by_latency_tier,
        get_latency_tier,
    )
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# ── Hardcoded fallback for max_output_tokens ──────────────────────────────────
# Used ONLY when the DB has no record for a model_id.
# Kept here (not in credit_precheck.py) so there's one place to maintain it.
_MAX_TOKENS_FALLBACK: dict[str, int] = {
    "gpt-4": 8192,
    "gpt-4-turbo": 4096,
    "gpt-4o": 4096,
    "gpt-4o-mini": 16384,
    "gpt-3.5-turbo": 4096,
    "claude-3-opus": 4096,
    "claude-3-sonnet": 4096,
    "claude-3-haiku": 4096,
    "claude-3-5-sonnet": 8192,
    "claude-sonnet-4": 8192,
    "llama-3": 8192,
    "llama-3.1": 128000,
    "llama-3.2": 128000,
    "mistral": 8192,
    "mixtral": 32768,
}
_DEFAULT_MAX_TOKENS = 4096

# ── Hardcoded fallback for tier pools (used when DB is empty) ─────────────────
_SMALL_TIER_FALLBACK = [
    "openai/gpt-4o-mini",
    "anthropic/claude-3-haiku",
    "google/gemini-flash-1.5",
    "deepseek/deepseek-chat",
    "mistral/mistral-small",
    "meta-llama/llama-3.1-8b-instant",
    "openai/gpt-4o",
]
_MEDIUM_TIER_FALLBACK = [
    *_SMALL_TIER_FALLBACK,
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-sonnet",
    "google/gemini-pro-1.5",
    "meta-llama/llama-3.1-70b",
    "meta-llama/llama-3.1-405b",
    "mistral/mistral-large",
    "cohere/command-r-plus",
    "deepseek/deepseek-coder",
]
_STABLE_FALLBACK = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-1.5-flash",
]
_FREE_MODELS_FALLBACK = {
    "google/gemini-2.0-flash-exp:free",
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "huggingfaceh4/zephyr-7b-beta:free",
    "openchat/openchat-7b:free",
    "nousresearch/nous-hermes-llama2-13b:free",
    "arcee-ai/trinity-mini:free",
}

# ── In-memory cache state ─────────────────────────────────────────────────────

# provider_model_id (lowercase) → max_output_tokens
_max_tokens: dict[str, int] = {}

# provider_model_id (lowercase) → True for models with json mode
_has_json_mode: set[str] = set()

# provider_model_id (lowercase) → True for reasoning models
_is_reasoning: set[str] = set()

# provider_model_id (lowercase) → True for free models
_free_models: set[str] = set()

# provider_model_id (lowercase) → latency tier (1-4)
_latency_tier: dict[str, int] = {}

# model_id (lowercase) → {task_type → score}
_quality_priors: dict[str, dict[str, float]] = {}

_cache_loaded: bool = False
_cache_loaded_at: float = 0.0
_CACHE_TTL: float = 900.0  # 15 minutes
_refresh_in_progress: bool = False


# ── Public loaders ────────────────────────────────────────────────────────────


def load_model_capabilities_cache(force: bool = False) -> None:
    """
    Load (or refresh) model capability data from the database into memory.

    Safe to call multiple times — skips reload if cache is fresh unless
    force=True. Intended to be called via asyncio.to_thread() from startup.py.

    When the DB is unavailable, hardcoded fallbacks are used so the service
    remains functional.
    """
    global _max_tokens, _has_json_mode, _is_reasoning, _free_models
    global _latency_tier, _quality_priors, _cache_loaded, _cache_loaded_at

    now = time.monotonic()
    if not force and _cache_loaded and (now - _cache_loaded_at) < _CACHE_TTL:
        logger.debug(
            "Model capabilities cache is fresh (%.0fs old), skipping reload",
            now - _cache_loaded_at,
        )
        return

    logger.info("Loading model capabilities cache from database...")

    try:
        from src.db.model_capabilities_db import (
            get_all_model_capability_flags,
            get_all_quality_scores,
        )

        # ── Capability flags ──────────────────────────────────────────────────
        capability_rows = get_all_model_capability_flags()

        new_max_tokens: dict[str, int] = {}
        new_has_json_mode: set[str] = set()
        new_is_reasoning: set[str] = set()
        new_free_models: set[str] = set()
        new_latency_tier: dict[str, int] = {}

        for row in capability_rows:
            model_id = (row.get("provider_model_id") or row.get("model_name") or "").lower()
            if not model_id:
                continue

            if row.get("max_output_tokens") is not None:
                new_max_tokens[model_id] = int(row["max_output_tokens"])
            if row.get("has_json_mode"):
                new_has_json_mode.add(model_id)
            if row.get("is_reasoning"):
                new_is_reasoning.add(model_id)
            if row.get("is_free"):
                new_free_models.add(model_id)
            if row.get("latency_tier") is not None:
                new_latency_tier[model_id] = int(row["latency_tier"])

        # ── Quality scores ────────────────────────────────────────────────────
        score_rows = get_all_quality_scores()

        new_quality_priors: dict[str, dict[str, float]] = {}
        for row in score_rows:
            model_id = (row.get("model_id") or "").lower()
            task_type = row.get("task_type") or ""
            score = row.get("score")
            if model_id and task_type and score is not None:
                if model_id not in new_quality_priors:
                    new_quality_priors[model_id] = {}
                new_quality_priors[model_id][task_type] = float(score)

        # ── Atomic swap ───────────────────────────────────────────────────────
        if capability_rows or score_rows:
            _max_tokens = new_max_tokens
            _has_json_mode = new_has_json_mode
            _is_reasoning = new_is_reasoning
            _free_models = new_free_models if new_free_models else _FREE_MODELS_FALLBACK.copy()
            _latency_tier = new_latency_tier
            _quality_priors = new_quality_priors
            _cache_loaded = True
            _cache_loaded_at = time.monotonic()

            logger.info(
                "Model capabilities cache loaded: %d models, %d free, %d quality scores",
                len(capability_rows),
                len(_free_models),
                sum(len(v) for v in _quality_priors.values()),
            )
        else:
            # DB returned nothing — use hardcoded fallbacks so service stays up
            _free_models = _FREE_MODELS_FALLBACK.copy()
            _cache_loaded = True
            _cache_loaded_at = time.monotonic()
            logger.warning(
                "Model capabilities DB returned no data; "
                "using hardcoded fallbacks for free_models and tier pools"
            )

    except Exception as e:
        logger.error("Failed to load model capabilities cache: %s", e)
        # Mark as loaded so _ensure_loaded() doesn't cascade blocking DB calls, but
        # set _cache_loaded_at=0 so TTL is immediately expired — the next access will
        # fire a non-blocking background retry via stale-while-revalidate logic.
        _free_models = _FREE_MODELS_FALLBACK.copy()
        _cache_loaded = True
        _cache_loaded_at = 0.0


def invalidate_model_capabilities_cache() -> None:
    """Force a reload on next access by resetting the loaded-at timestamp."""
    global _cache_loaded_at
    _cache_loaded_at = 0.0
    logger.info("Model capabilities cache invalidated — will reload on next access")


# ── Stale-while-revalidate refresh ───────────────────────────────────────────


def _ensure_loaded() -> None:
    """
    Ensure the cache has been loaded at least once (blocking on first call only).

    On TTL expiry, fires a non-blocking background refresh and returns
    immediately with slightly stale data — never blocks the event loop.
    """
    global _refresh_in_progress

    now = time.monotonic()
    if not _cache_loaded:
        # Initial load: if we're on the event loop (e.g., called from a background
        # task constructor during startup), schedule non-blocking to avoid stalling
        # all async work during the critical startup window.
        # If we're in a thread (e.g., startup's asyncio.to_thread call), block fine.
        try:
            loop = asyncio.get_running_loop()
            if not _refresh_in_progress:
                _refresh_in_progress = True
                loop.create_task(_refresh_cache_background())
        except RuntimeError:
            # No event loop — safe to block (called from a worker thread)
            load_model_capabilities_cache()
        return

    if (now - _cache_loaded_at) >= _CACHE_TTL:
        if not _refresh_in_progress:
            _refresh_in_progress = True
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_refresh_cache_background())
            except RuntimeError:
                # Not in an async context — refresh synchronously
                _refresh_in_progress = False
                load_model_capabilities_cache()


async def _refresh_cache_background() -> None:
    global _refresh_in_progress
    try:
        await asyncio.to_thread(load_model_capabilities_cache)
    except Exception as e:
        logger.error("Background model capabilities cache refresh failed: %s", e)
    finally:
        _refresh_in_progress = False


# ── Public accessors ──────────────────────────────────────────────────────────


def get_max_output_tokens(model_id: str, default: int = _DEFAULT_MAX_TOKENS) -> int:
    """
    Return the maximum output tokens for model_id.

    Falls back to the hardcoded _MAX_TOKENS_FALLBACK dict using partial
    matching (e.g. "gpt-4o-mini" matches key "gpt-4o-mini"), then to
    `default` if no match is found.
    """
    _ensure_loaded()
    key = model_id.lower()

    # Exact match from DB
    if key in _max_tokens:
        return _max_tokens[key]

    # Suffix match from DB: "gpt-4o-mini" key matches "openai/gpt-4o-mini" query.
    # Use "/" as segment boundary to avoid "gpt-4" matching "gpt-4o".
    for db_key, tokens in _max_tokens.items():
        if key.endswith(f"/{db_key}") or db_key.endswith(f"/{key}"):
            return tokens

    # Hardcoded fallback — word-boundary-aware segment match.
    # Strip the provider prefix first ("openai/gpt-4o-mini" → "gpt-4o-mini").
    # Sort longest-first so "gpt-4o" wins over "gpt-4" for input "gpt-4o-mini".
    model_suffix = key.split("/")[-1]
    for pattern, tokens in sorted(_MAX_TOKENS_FALLBACK.items(), key=lambda x: -len(x[0])):
        pat = pattern.lower()
        if model_suffix == pat or (
            model_suffix.startswith(pat)
            and len(model_suffix) > len(pat)
            and not model_suffix[len(pat)].isalnum()
        ):
            return tokens

    return default


def get_free_models() -> set[str]:
    """Return a snapshot copy of model IDs that are free (is_free=true)."""
    _ensure_loaded()
    return _free_models.copy() if _free_models else _FREE_MODELS_FALLBACK.copy()


def is_free_model(model_id: str) -> bool:
    """Return True if model_id is a free model (case-insensitive)."""
    _ensure_loaded()
    key = model_id.lower()
    free = _free_models if _free_models else _FREE_MODELS_FALLBACK
    # Exact match or ends with :free
    return key in free or key.endswith(":free")


def get_latency_tier(model_id: str, default: int = 3) -> int:
    """Return the latency tier (1-4) for model_id."""
    _ensure_loaded()
    key = model_id.lower()
    return _latency_tier.get(key, default)


def get_models_by_latency_tier(max_tier: int = 2) -> list[str]:
    """
    Return model IDs with latency_tier <= max_tier.

    Falls back to hardcoded tier pools when the DB cache is empty.
      max_tier=1 → ultra-fast only
      max_tier=2 → fast + ultra (SMALL_TIER equivalent)
      max_tier=3 → all but slow
    """
    _ensure_loaded()

    if _latency_tier:
        return [mid for mid, tier in _latency_tier.items() if tier <= max_tier]

    # Fallback to hardcoded lists
    if max_tier <= 1:
        return []  # No ultra-fast list in fallback
    if max_tier <= 2:
        return list(_SMALL_TIER_FALLBACK)
    return list(_MEDIUM_TIER_FALLBACK)


def get_stable_models() -> list[str]:
    """
    Return a curated list of known-stable models for fail-open fallback.

    Uses tier-2 models from DB, or the hardcoded STABLE_FALLBACK list.
    """
    _ensure_loaded()

    if _latency_tier:
        tier2 = [mid for mid, tier in _latency_tier.items() if tier <= 2]
        return tier2 if tier2 else _STABLE_FALLBACK
    return list(_STABLE_FALLBACK)


def get_quality_priors() -> dict[str, dict[str, float]]:
    """
    Return a snapshot copy of quality priors: {model_id: {task_type: score}}.

    Falls back to an empty dict if DB has no scores yet (select_model
    handles the empty-dict case gracefully via .get()).
    """
    _ensure_loaded()
    return {model: dict(tasks) for model, tasks in _quality_priors.items()}


def has_json_mode(model_id: str) -> bool:
    """Return True if model supports JSON output mode."""
    _ensure_loaded()
    return model_id.lower() in _has_json_mode


def is_reasoning_model(model_id: str) -> bool:
    """Return True if model is a reasoning/thinking model."""
    _ensure_loaded()
    return model_id.lower() in _is_reasoning
