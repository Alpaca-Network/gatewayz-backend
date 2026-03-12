"""
Pricing Audit Service

Compares live provider pricing against database pricing to detect mismatches.

Flow:
1. Fetch live catalog from each provider's API (OpenRouter, DeepInfra, etc.)
2. Fetch all models from the database via get_all_models_for_catalog()
3. Normalize IDs on both sides (the DB applies clean_model_name during sync)
4. Match models using multi-pass ID normalization
5. Compare pricing per model, grouped by provider/gateway
6. Flag models where DB pricing differs from provider pricing
7. Report unmatched models with reasons

All pricing is compared in per-token format (canonical system format).
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_PERCENT = 0.0


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class PricingMismatch:
    """A single model with mismatched pricing between provider and database."""

    model_id: str
    db_model_id: str
    gateway: str
    field: str  # "prompt" or "completion"
    provider_price: str
    db_price: str
    difference_percent: float
    provider_price_per_million: str
    db_price_per_million: str


@dataclass
class UnmatchedModel:
    """A model that exists only in one source (provider or DB)."""

    model_id: str
    source: str  # "provider" or "database"
    reason: str
    pricing: dict[str, str] | None = None


@dataclass
class MatchedModel:
    """A model that was matched between provider and DB."""

    provider_id: str
    db_id: str
    match_method: str  # "exact", "normalized", "cleaned"
    pricing_status: str  # "match", "mismatch", "no_provider_pricing"


@dataclass
class GatewayAuditResult:
    """Audit result for a single gateway/provider."""

    gateway: str
    gateway_display_name: str
    provider_model_count: int = 0
    db_model_count: int = 0
    total_models: int = 0
    models_with_pricing: int = 0
    models_without_pricing: int = 0
    models_matched: int = 0
    models_mismatched: int = 0
    models_only_in_provider: int = 0
    models_only_in_db: int = 0
    mismatches: list[PricingMismatch] = field(default_factory=list)
    unmatched_provider: list[UnmatchedModel] = field(default_factory=list)
    unmatched_db: list[UnmatchedModel] = field(default_factory=list)
    matched_models: list[MatchedModel] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PricingAuditReport:
    """Full audit report across all gateways."""

    timestamp: str = ""
    duration_seconds: float = 0.0
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
    total_models_audited: int = 0
    total_mismatches: int = 0
    total_missing_in_db: int = 0
    total_missing_in_provider: int = 0
    total_missing_pricing: int = 0
    gateways: dict[str, GatewayAuditResult] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


# ── ID normalization ────────────────────────────────────────────────────────


def _normalize_id(model_id: str) -> str:
    """Normalize a model ID for matching.

    Applies the same transformations that clean_model_name does during sync:
    - Strip whitespace
    - Remove colons (company prefix stripping)
    - Remove parenthetical info like (FP8), (7B), (free)
    - Collapse whitespace
    - Lowercase
    """
    if not model_id:
        return ""

    s = model_id.strip()

    # Remove known variant suffixes after colon (e.g., :free, :extended, :thinking)
    # But DON'T strip the prefix before colon (that would turn "org/model:free" into "free")
    s = re.sub(r":(free|extended|thinking|beta|online|floor)$", "", s, flags=re.IGNORECASE)

    # For colon-prefixed IDs like "nousresearch:hermes-3", strip the prefix
    # Only if the part before colon looks like a short org prefix (no slash)
    if ":" in s and "/" not in s.split(":")[0]:
        parts = s.split(":", 1)
        if parts[1].strip():
            s = parts[1].strip()

    # Remove parenthetical info at end: (FP8), (7B), (free), etc.
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)

    # Collapse whitespace and lowercase
    s = " ".join(s.split()).lower()

    return s


def _make_slug(model_id: str) -> str:
    """Create a slug from a model ID for fuzzy matching.

    Strips everything non-alphanumeric except forward slashes (to keep org/model),
    lowercases, collapses separators.  Also strips known suffixes like :free, :extended.
    """
    if not model_id:
        return ""

    s = model_id.lower()
    # Strip known variant suffixes (OpenRouter uses :free, :extended, :thinking etc.)
    s = re.sub(r":(free|extended|thinking|beta|online|floor)\b", "", s)
    # Keep alphanumeric, forward slash, dots, and dashes
    s = re.sub(r"[^a-z0-9/.\-]", "", s)
    return s


def _match_models(
    provider_models: list[dict],
    db_models: list[dict],
) -> tuple[
    list[tuple[dict, dict, str]],  # matched: (provider_model, db_model, method)
    list[dict],  # unmatched provider models
    list[dict],  # unmatched DB models
]:
    """Multi-pass matching of provider models to DB models.

    Pass 1: Exact match on raw ID
    Pass 2: Normalized match (lowercase, strip colons/parens, collapse whitespace)
    Pass 3: Slug match (alphanumeric-only comparison)

    Returns (matched_pairs, unmatched_provider, unmatched_db).
    """
    matched: list[tuple[dict, dict, str]] = []
    remaining_provider: dict[str, dict] = {}
    remaining_db: dict[str, dict] = {}

    # Index all models
    for m in provider_models:
        mid = m.get("id", "")
        if mid:
            remaining_provider[mid] = m

    for m in db_models:
        mid = m.get("id", "")
        if mid:
            remaining_db[mid] = m

    # Pass 1: Exact match
    exact_matches = set(remaining_provider.keys()) & set(remaining_db.keys())
    for mid in exact_matches:
        matched.append((remaining_provider.pop(mid), remaining_db.pop(mid), "exact"))

    # Pass 2: Normalized match
    if remaining_provider and remaining_db:
        prov_norm: dict[str, list[str]] = {}
        for pid in remaining_provider:
            norm = _normalize_id(pid)
            prov_norm.setdefault(norm, []).append(pid)

        db_norm: dict[str, list[str]] = {}
        for did in remaining_db:
            norm = _normalize_id(did)
            db_norm.setdefault(norm, []).append(did)

        norm_matches = set(prov_norm.keys()) & set(db_norm.keys())
        for norm_key in norm_matches:
            # Take first match from each side
            pid = prov_norm[norm_key][0]
            did = db_norm[norm_key][0]
            if pid in remaining_provider and did in remaining_db:
                matched.append((remaining_provider.pop(pid), remaining_db.pop(did), "normalized"))

    # Pass 3: Slug match (most aggressive)
    if remaining_provider and remaining_db:
        prov_slug: dict[str, list[str]] = {}
        for pid in remaining_provider:
            slug = _make_slug(pid)
            prov_slug.setdefault(slug, []).append(pid)

        db_slug: dict[str, list[str]] = {}
        for did in remaining_db:
            slug = _make_slug(did)
            db_slug.setdefault(slug, []).append(did)

        slug_matches = set(prov_slug.keys()) & set(db_slug.keys())
        for slug_key in slug_matches:
            pid = prov_slug[slug_key][0]
            did = db_slug[slug_key][0]
            if pid in remaining_provider and did in remaining_db:
                matched.append((remaining_provider.pop(pid), remaining_db.pop(did), "cleaned"))

    # Pass 4: Strip gateway prefix match
    # Handles cases like DB="groq/allam-2-7b" vs API="allam-2-7b"
    # or DB="groq/meta-llama/llama-4-scout" vs API="meta-llama/llama-4-scout"
    # or DB="groq/groq/compound-mini" vs API="groq/compound-mini"
    if remaining_provider and remaining_db:
        # Collect all gateway slugs that appear as prefixes in DB IDs
        db_prefixes: set[str] = set()
        for did in remaining_db:
            if "/" in did:
                prefix = did.split("/", 1)[0].lower()
                db_prefixes.add(prefix)

        def _strip_all_prefixes(model_id: str, prefixes: set[str]) -> list[str]:
            """Generate candidate IDs by stripping gateway prefixes.

            Returns multiple candidates to handle:
            - groq/model → model
            - groq/groq/model → groq/model, model
            - model → groq/model (add prefix)
            """
            candidates = [model_id.lower()]
            parts = model_id.split("/", 1)
            if len(parts) == 2 and parts[0].lower() in prefixes:
                stripped = parts[1]
                candidates.append(stripped.lower())
                # Double prefix: groq/groq/x → x
                inner_parts = stripped.split("/", 1)
                if len(inner_parts) == 2 and inner_parts[0].lower() == parts[0].lower():
                    candidates.append(inner_parts[1].lower())
            else:
                # Try adding each prefix: model → groq/model
                for prefix in prefixes:
                    candidates.append(f"{prefix}/{model_id}".lower())
            return candidates

        # Build lookup indices for both sides
        prov_by_lower: dict[str, str] = {}  # lowered_id → original_id
        for pid in remaining_provider:
            for cand in _strip_all_prefixes(pid, db_prefixes):
                if cand not in prov_by_lower:
                    prov_by_lower[cand] = pid

        db_by_lower: dict[str, str] = {}
        for did in remaining_db:
            for cand in _strip_all_prefixes(did, db_prefixes):
                if cand not in db_by_lower:
                    db_by_lower[cand] = did

        # Match: for each provider candidate, check if any DB candidate matches
        matched_prov_ids: set[str] = set()
        matched_db_ids: set[str] = set()
        for cand, pid in prov_by_lower.items():
            if pid in matched_prov_ids:
                continue
            if cand in db_by_lower:
                did = db_by_lower[cand]
                if did in matched_db_ids:
                    continue
                if pid in remaining_provider and did in remaining_db:
                    matched.append(
                        (remaining_provider.pop(pid), remaining_db.pop(did), "strip_prefix")
                    )
                    matched_prov_ids.add(pid)
                    matched_db_ids.add(did)

    return (
        matched,
        list(remaining_provider.values()),
        list(remaining_db.values()),
    )


# ── Pricing helpers ─────────────────────────────────────────────────────────


def _to_decimal(value: Any) -> Decimal | None:
    """Safely convert a pricing value to Decimal. Returns None for missing data."""
    if value is None or value == "":
        return None
    if value == "0" or value == 0:
        return Decimal("0")
    try:
        d = Decimal(str(value))
        return d if d >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _per_million(per_token: Decimal) -> str:
    """Convert per-token price to per-million-token string for readability."""
    return str((per_token * Decimal("1000000")).quantize(Decimal("0.0001")))


def _calc_diff_percent(provider_val: Decimal, db_val: Decimal) -> float:
    """Calculate percentage difference between two prices."""
    if provider_val == 0 and db_val == 0:
        return 0.0
    if provider_val == 0:
        return 100.0
    diff = abs(provider_val - db_val)
    return float((diff / provider_val) * 100)


def _extract_pricing(model: dict) -> dict[str, Decimal | None]:
    """Extract prompt and completion pricing from a model dict."""
    pricing = model.get("pricing") or {}
    return {
        "prompt": _to_decimal(pricing.get("prompt")),
        "completion": _to_decimal(pricing.get("completion")),
    }


def _pricing_summary(model: dict) -> dict[str, str] | None:
    """Get a readable pricing summary for reporting unmatched models."""
    pricing = model.get("pricing") or {}
    prompt = pricing.get("prompt")
    completion = pricing.get("completion")
    if prompt is None and completion is None:
        return None
    result = {}
    if prompt is not None:
        p = _to_decimal(prompt)
        result["prompt_per_million"] = _per_million(p) if p else "0"
    if completion is not None:
        c = _to_decimal(completion)
        result["completion_per_million"] = _per_million(c) if c else "0"
    return result


def _guess_unmatched_reason(model_id: str, source: str, other_ids: set[str]) -> str:
    """Guess why a model wasn't matched."""
    slug = _make_slug(model_id)

    # Check if there's a near-match in the other side
    for other_id in other_ids:
        other_slug = _make_slug(other_id)

        # Very close slug match (off by small diff)
        if slug and other_slug and (slug in other_slug or other_slug in slug):
            if source == "provider":
                return f"Likely matches DB model '{other_id}' but IDs differ slightly"
            else:
                return f"Likely matches provider model '{other_id}' but IDs differ slightly"

    if source == "provider":
        if ":" in model_id:
            return "Contains colon in ID (stripped by DB clean_model_name during sync) — may exist under cleaned name"
        if "(" in model_id:
            return "Contains parenthetical info (stripped by DB clean_model_name) — may exist under cleaned name"
        return (
            "Model exists in provider API but not found in database — may not have been synced yet"
        )
    else:
        return "Model exists in database but provider no longer lists it — may have been removed or renamed"


# ── Data fetchers ───────────────────────────────────────────────────────────


def _get_all_gateways() -> dict[str, str]:
    """Get all auditable gateway slugs and display names."""
    from src.routes.catalog import GATEWAY_REGISTRY
    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

    return {
        slug: info["name"]
        for slug, info in GATEWAY_REGISTRY.items()
        if slug in PROVIDER_FETCH_FUNCTIONS
    }


def _fetch_provider_models(gateway: str) -> list[dict] | None:
    """Fetch live models directly from the provider's API.

    Uses lightweight raw HTTP calls to avoid the enrich_model_with_pricing()
    circular dependency that exists in the normal PROVIDER_FETCH_FUNCTIONS.

    Returns None on error (distinguishes from an empty catalog).
    """
    try:
        return _raw_fetch_provider(gateway)
    except Exception as e:
        logger.error(f"Failed to fetch live provider models for {gateway}: {e}")
        return None


# ── Raw provider API fetchers ──────────────────────────────────────────────
# These bypass the normal fetch_models_from_* functions to avoid the circular
# dependency: fetch → enrich_model_with_pricing → _build_openrouter_pricing_index
# → get_cached_models → transform_db_models_batch → _build_openrouter_pricing_index → ...


def _raw_fetch_provider(gateway: str) -> list[dict]:
    """Dispatch to the appropriate raw provider fetcher."""
    import httpx

    from src.config.config import Config
    from src.services.pricing_normalization import (
        get_provider_format,
        normalize_to_per_token,
    )

    provider_format = get_provider_format(gateway)

    # ── Provider API configs ──────────────────────────────────────────────
    # Each entry: (url, headers, response_parser, pricing_extractor)

    PROVIDER_CONFIGS: dict[str, dict] = {
        "openrouter": {
            "url": "https://openrouter.ai/api/v1/models",
            "headers": {},  # No auth needed for model list
        },
        "deepinfra": {
            "url": "https://api.deepinfra.com/models/list",
            "key_attr": "DEEPINFRA_API_KEY",
        },
        "together": {
            "url": "https://api.together.xyz/v1/models",
            "key_attr": "TOGETHER_API_KEY",
        },
        "groq": {
            "url": "https://api.groq.com/openai/v1/models",
            "key_attr": "GROQ_API_KEY",
        },
        "fireworks": {
            "url": "https://api.fireworks.ai/inference/v1/models",
            "key_attr": "FIREWORKS_API_KEY",
        },
        "featherless": {
            "url": "https://api.featherless.ai/v1/models",
            "key_attr": "FEATHERLESS_API_KEY",
        },
        "cerebras": {
            "url": "https://api.cerebras.ai/v1/models",
            "key_attr": "CEREBRAS_API_KEY",
        },
        "xai": {
            "url": "https://api.x.ai/v1/models",
            "key_attr": "XAI_API_KEY",
        },
        "novita": {
            "url": "https://api.novita.ai/v3/openai/models",
            "key_attr": "NOVITA_API_KEY",
        },
        "nebius": {
            "url": "https://api.studio.nebius.ai/v1/models",
            "key_attr": "NEBIUS_API_KEY",
        },
        "cohere": {
            "url": "https://api.cohere.com/v2/models",
            "key_attr": "COHERE_API_KEY",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
        },
    }

    config = PROVIDER_CONFIGS.get(gateway)
    if not config:
        logger.info(f"No raw fetch config for '{gateway}', skipping")
        return []

    url = config["url"]
    key_attr = config.get("key_attr")
    headers = config.get("headers", {})

    if key_attr:
        api_key = getattr(Config, key_attr, None)
        if not api_key:
            logger.warning(f"API key {key_attr} not configured for {gateway}")
            return []
        auth_header = config.get("auth_header", "Authorization")
        auth_prefix = config.get("auth_prefix", "Bearer ")
        headers[auth_header] = f"{auth_prefix}{api_key}"

    headers.setdefault("Content-Type", "application/json")

    try:
        response = httpx.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"HTTP error fetching {gateway} models: {e}")
        return []

    payload = response.json()

    # Parse response — most return {"data": [...]} or direct array
    if isinstance(payload, list):
        raw_models = payload
    elif isinstance(payload, dict):
        raw_models = payload.get("data", payload.get("models", []))
    else:
        return []

    # Extract model ID and pricing
    models: list[dict] = []
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue

        # Different providers use different ID fields
        model_id = raw.get("id") or raw.get("model_name") or ""
        if not model_id:
            continue

        pricing = _extract_raw_pricing(raw, gateway, provider_format, normalize_to_per_token)

        models.append(
            {
                "id": model_id,
                "pricing": pricing,
            }
        )

    logger.info(f"Raw fetched {len(models)} models from {gateway} API")
    return models


def _extract_raw_pricing(
    raw: dict,
    gateway: str,
    provider_format: str,
    normalize_fn,
) -> dict[str, str | None]:
    """Extract and normalize pricing from a raw provider API response model."""
    pricing: dict[str, str | None] = {"prompt": None, "completion": None}

    raw_pricing = raw.get("pricing") or {}

    if gateway == "openrouter":
        # OpenRouter: pricing.prompt / pricing.completion — already per-token
        prompt = raw_pricing.get("prompt")
        completion = raw_pricing.get("completion")
        if prompt is not None and str(prompt) not in ("-1", ""):
            pricing["prompt"] = str(prompt)
        if completion is not None and str(completion) not in ("-1", ""):
            pricing["completion"] = str(completion)
        return pricing

    if gateway == "deepinfra":
        # DeepInfra API: cents_per_input_token / cents_per_output_token
        # Values are in cents per 1M tokens. Convert cents→dollars (/100),
        # then normalize from per-1M to per-token via normalize_fn.
        cents_in = raw_pricing.get("cents_per_input_token")
        cents_out = raw_pricing.get("cents_per_output_token")
        if cents_in is not None:
            try:
                dollars_per_1m = float(Decimal(str(cents_in)) / Decimal("100"))
                normalized = normalize_fn(dollars_per_1m, provider_format)
                if normalized is not None:
                    pricing["prompt"] = str(normalized)
            except (InvalidOperation, ValueError):
                pass  # Skip malformed pricing values
        if cents_out is not None:
            try:
                dollars_per_1m = float(Decimal(str(cents_out)) / Decimal("100"))
                normalized = normalize_fn(dollars_per_1m, provider_format)
                if normalized is not None:
                    pricing["completion"] = str(normalized)
            except (InvalidOperation, ValueError):
                pass  # Skip malformed pricing values
        return pricing

    # Generic pattern: pricing.input/output or pricing.prompt/completion
    # For most providers: values are per-1M tokens, need normalization to per-token
    prompt_val = (
        raw_pricing.get("prompt")
        if raw_pricing.get("prompt") is not None
        else raw_pricing.get("input")
    )
    completion_val = (
        raw_pricing.get("completion")
        if raw_pricing.get("completion") is not None
        else raw_pricing.get("output")
    )

    # Some providers use cents_per_input_token format (groq, fireworks)
    if prompt_val is None:
        cents_in = raw_pricing.get("cents_per_input_token")
        if cents_in is not None:
            try:
                prompt_val = float(cents_in) / 100  # cents → dollars per 1M
            except (ValueError, TypeError):
                pass  # Skip non-numeric pricing values

    if completion_val is None:
        cents_out = raw_pricing.get("cents_per_output_token")
        if cents_out is not None:
            try:
                completion_val = float(cents_out) / 100  # cents → dollars per 1M
            except (ValueError, TypeError):
                pass  # Skip non-numeric pricing values

    # Normalize to per-token
    if prompt_val is not None:
        normalized = normalize_fn(prompt_val, provider_format)
        if normalized is not None:
            pricing["prompt"] = str(normalized)

    if completion_val is not None:
        normalized = normalize_fn(completion_val, provider_format)
        if normalized is not None:
            pricing["completion"] = str(normalized)

    return pricing


def _fetch_db_models_by_gateway() -> dict[str, list[dict]]:
    """Fetch all models from the database, grouped by provider slug."""
    from src.db.models_catalog_db import get_all_models_for_catalog

    db_models_raw = get_all_models_for_catalog(include_inactive=False)
    grouped: dict[str, list[dict]] = {}

    for raw_model in db_models_raw:
        try:
            provider = raw_model.get("providers") or {}
            slug = provider.get("slug", "unknown")

            from src.services.pricing_normalization import (
                get_provider_format,
                normalize_to_per_token,
            )

            metadata = raw_model.get("metadata") or {}
            pricing_raw = metadata.get("pricing_raw") if isinstance(metadata, dict) else None
            is_migrated = raw_model.get("pricing_format_migrated", False)
            provider_fmt = get_provider_format(slug)
            pricing: dict[str, str | None] = {}

            if pricing_raw and isinstance(pricing_raw, dict):
                for field_name in ("prompt", "completion"):
                    val = pricing_raw.get(field_name)
                    if val is not None:
                        if is_migrated:
                            # Already per-token format
                            pricing[field_name] = str(val)
                        else:
                            # Stored in provider's native format — normalize
                            norm = normalize_to_per_token(val, provider_fmt)
                            pricing[field_name] = str(norm) if norm is not None else None

            # Fallback: use pricing_original_* columns if pricing_raw is empty
            # These columns store values in the PROVIDER's native format
            if not pricing.get("prompt") and raw_model.get("pricing_original_prompt") is not None:
                norm = normalize_to_per_token(raw_model["pricing_original_prompt"], provider_fmt)
                pricing["prompt"] = str(norm) if norm is not None else None
            if (
                not pricing.get("completion")
                and raw_model.get("pricing_original_completion") is not None
            ):
                norm = normalize_to_per_token(
                    raw_model["pricing_original_completion"], provider_fmt
                )
                pricing["completion"] = str(norm) if norm is not None else None

            model_entry = {
                "id": raw_model.get("provider_model_id", raw_model.get("model_name", "")),
                "model_name": raw_model.get("model_name", ""),
                "provider_slug": slug,
                "pricing": pricing if pricing else None,
            }

            grouped.setdefault(slug, []).append(model_entry)
        except Exception as e:
            logger.warning(f"Failed to process DB model: {e}")

    return grouped


# ── Gateway audit ───────────────────────────────────────────────────────────


def _audit_gateway(
    gateway: str,
    display_name: str,
    provider_models: list[dict],
    db_models: list[dict],
    threshold: float,
) -> GatewayAuditResult:
    """Compare provider models against DB models for a single gateway."""
    result = GatewayAuditResult(
        gateway=gateway,
        gateway_display_name=display_name,
        provider_model_count=len(provider_models),
        db_model_count=len(db_models),
    )

    # Multi-pass matching
    matched_pairs, unmatched_prov, unmatched_db = _match_models(provider_models, db_models)

    result.total_models = len(matched_pairs) + len(unmatched_prov) + len(unmatched_db)
    result.models_only_in_provider = len(unmatched_prov)
    result.models_only_in_db = len(unmatched_db)

    # Build ID sets for reason guessing
    all_prov_ids = {m.get("id", "") for m in provider_models}
    all_db_ids = {m.get("id", "") for m in db_models}

    # Process unmatched provider models
    for m in sorted(unmatched_prov, key=lambda x: x.get("id", "")):
        mid = m.get("id", "")
        result.unmatched_provider.append(
            UnmatchedModel(
                model_id=mid,
                source="provider",
                reason=_guess_unmatched_reason(mid, "provider", all_db_ids),
                pricing=_pricing_summary(m),
            )
        )

    # Process unmatched DB models
    for m in sorted(unmatched_db, key=lambda x: x.get("id", "")):
        mid = m.get("id", "")
        result.unmatched_db.append(
            UnmatchedModel(
                model_id=mid,
                source="database",
                reason=_guess_unmatched_reason(mid, "database", all_prov_ids),
                pricing=_pricing_summary(m),
            )
        )

    # Compare pricing for matched models
    for prov_model, db_model, match_method in matched_pairs:
        prov_pricing = _extract_pricing(prov_model)
        db_pricing = _extract_pricing(db_model)

        prov_id = prov_model.get("id", "")
        db_id = db_model.get("id", "")

        # Skip models where provider has no pricing data
        if prov_pricing["prompt"] is None and prov_pricing["completion"] is None:
            result.models_without_pricing += 1
            result.matched_models.append(
                MatchedModel(
                    provider_id=prov_id,
                    db_id=db_id,
                    match_method=match_method,
                    pricing_status="no_provider_pricing",
                )
            )
            continue

        result.models_with_pricing += 1
        has_mismatch = False

        for price_field in ("prompt", "completion"):
            prov_val = prov_pricing.get(price_field)
            db_val = db_pricing.get(price_field)

            if prov_val is None:
                continue

            if db_val is None:
                if prov_val > 0:
                    has_mismatch = True
                    result.mismatches.append(
                        PricingMismatch(
                            model_id=prov_id,
                            db_model_id=db_id,
                            gateway=gateway,
                            field=price_field,
                            provider_price=str(prov_val),
                            db_price="MISSING",
                            difference_percent=100.0,
                            provider_price_per_million=_per_million(prov_val),
                            db_price_per_million="MISSING",
                        )
                    )
                continue

            diff_pct = _calc_diff_percent(prov_val, db_val)

            if diff_pct > threshold:
                has_mismatch = True
                result.mismatches.append(
                    PricingMismatch(
                        model_id=prov_id,
                        db_model_id=db_id,
                        gateway=gateway,
                        field=price_field,
                        provider_price=str(prov_val),
                        db_price=str(db_val),
                        difference_percent=round(diff_pct, 2),
                        provider_price_per_million=_per_million(prov_val),
                        db_price_per_million=_per_million(db_val),
                    )
                )

        if has_mismatch:
            result.models_mismatched += 1
            result.matched_models.append(
                MatchedModel(
                    provider_id=prov_id,
                    db_id=db_id,
                    match_method=match_method,
                    pricing_status="mismatch",
                )
            )
        else:
            result.models_matched += 1
            result.matched_models.append(
                MatchedModel(
                    provider_id=prov_id,
                    db_id=db_id,
                    match_method=match_method,
                    pricing_status="match",
                )
            )

    return result


# ── Main entry point ────────────────────────────────────────────────────────


def run_pricing_audit(
    gateways: list[str] | None = None,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
) -> dict[str, Any]:
    """
    Run a full pricing audit comparing provider catalogs against database pricing.

    Args:
        gateways: Specific gateways to audit (None = all gateways).
        threshold_percent: Minimum percentage difference to flag as mismatch.

    Returns:
        Audit report as a serializable dictionary, grouped by gateway.
    """
    from datetime import UTC, datetime

    start_time = time.monotonic()
    report = PricingAuditReport(
        timestamp=datetime.now(UTC).isoformat(),
        threshold_percent=threshold_percent,
    )

    all_gateways = _get_all_gateways()
    if gateways:
        target_gateways = {g: all_gateways.get(g, g) for g in gateways if g in all_gateways}
    else:
        target_gateways = all_gateways

    logger.info(f"Starting pricing audit for {len(target_gateways)} gateways")

    # Step 1: Fetch all DB models grouped by provider
    logger.info("Fetching all models from database...")
    db_models_by_gateway = _fetch_db_models_by_gateway()
    logger.info(
        f"Fetched {sum(len(v) for v in db_models_by_gateway.values())} models "
        f"from {len(db_models_by_gateway)} providers in database"
    )

    # Step 2: Fetch provider models in parallel
    logger.info("Fetching provider catalogs in parallel...")
    provider_models_by_gateway: dict[str, list[dict]] = {}
    fetch_errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_provider_models, gw): gw for gw in target_gateways}
        try:
            for future in as_completed(futures, timeout=180):
                gw = futures[future]
                try:
                    models = future.result(timeout=30)
                    if models is None:
                        provider_models_by_gateway[gw] = []
                        fetch_errors[gw] = f"Failed to fetch models from {gw} API"
                    else:
                        provider_models_by_gateway[gw] = models
                        logger.info(f"Fetched {len(models)} models from provider API: {gw}")
                except Exception as e:
                    provider_models_by_gateway[gw] = []
                    fetch_errors[gw] = f"Provider fetch exception: {e}"
                    logger.warning(f"Failed to fetch provider models for {gw}: {e}")
        except TimeoutError:
            logger.warning("Provider fetch timed out after 180s; proceeding with partial results")
            for gw in target_gateways:
                if gw not in provider_models_by_gateway:
                    provider_models_by_gateway[gw] = []
                    fetch_errors[gw] = "Provider fetch timed out"

    # Step 3: Audit each gateway
    for gw, display_name in sorted(target_gateways.items()):
        provider_models = provider_models_by_gateway.get(gw, [])
        db_models = db_models_by_gateway.get(gw, [])

        # Check aliases (e.g., "hug" -> "huggingface")
        if not db_models:
            from src.routes.catalog import GATEWAY_REGISTRY

            registry_entry = GATEWAY_REGISTRY.get(gw, {})
            for alias in registry_entry.get("aliases", []):
                db_models = db_models_by_gateway.get(alias, [])
                if db_models:
                    break

        gateway_result = _audit_gateway(
            gateway=gw,
            display_name=display_name,
            provider_models=provider_models,
            db_models=db_models,
            threshold=threshold_percent,
        )

        # Surface any fetch errors into the gateway result
        if gw in fetch_errors:
            gateway_result.errors.append(fetch_errors[gw])

        report.gateways[gw] = gateway_result
        report.total_models_audited += gateway_result.total_models
        report.total_mismatches += gateway_result.models_mismatched
        report.total_missing_in_db += gateway_result.models_only_in_provider
        report.total_missing_in_provider += gateway_result.models_only_in_db
        report.total_missing_pricing += gateway_result.models_without_pricing

    report.duration_seconds = round(time.monotonic() - start_time, 2)

    report.summary = {
        "gateways_audited": len(report.gateways),
        "total_models_audited": report.total_models_audited,
        "total_models_with_mismatches": report.total_mismatches,
        "total_models_missing_in_db": report.total_missing_in_db,
        "total_models_only_in_db": report.total_missing_in_provider,
        "total_models_missing_pricing": report.total_missing_pricing,
        "threshold_percent": report.threshold_percent,
        "duration_seconds": report.duration_seconds,
    }

    return _serialize_report(report)


# ── Serialization ───────────────────────────────────────────────────────────


def _serialize_report(report: PricingAuditReport) -> dict[str, Any]:
    """Convert the dataclass report to a JSON-serializable dictionary."""
    result: dict[str, Any] = {
        "timestamp": report.timestamp,
        "duration_seconds": report.duration_seconds,
        "threshold_percent": report.threshold_percent,
        "summary": report.summary,
        "gateways": {},
    }

    for gw, gw_result in report.gateways.items():
        mismatches_list = []
        for m in gw_result.mismatches:
            mismatches_list.append(
                {
                    "model_id": m.model_id,
                    "db_model_id": m.db_model_id,
                    "field": m.field,
                    "provider_price_per_token": m.provider_price,
                    "db_price_per_token": m.db_price,
                    "provider_price_per_million": m.provider_price_per_million,
                    "db_price_per_million": m.db_price_per_million,
                    "difference_percent": m.difference_percent,
                }
            )

        unmatched_provider_list = []
        for u in gw_result.unmatched_provider:
            unmatched_provider_list.append(
                {
                    "model_id": u.model_id,
                    "reason": u.reason,
                    "pricing": u.pricing,
                }
            )

        unmatched_db_list = []
        for u in gw_result.unmatched_db:
            unmatched_db_list.append(
                {
                    "model_id": u.model_id,
                    "reason": u.reason,
                    "pricing": u.pricing,
                }
            )

        matched_list = []
        for m in gw_result.matched_models:
            matched_list.append(
                {
                    "provider_id": m.provider_id,
                    "db_id": m.db_id,
                    "match_method": m.match_method,
                    "pricing_status": m.pricing_status,
                }
            )

        result["gateways"][gw] = {
            "display_name": gw_result.gateway_display_name,
            "provider_model_count": gw_result.provider_model_count,
            "db_model_count": gw_result.db_model_count,
            "total_models": gw_result.total_models,
            "models_with_pricing": gw_result.models_with_pricing,
            "models_without_pricing": gw_result.models_without_pricing,
            "models_matched": gw_result.models_matched,
            "models_mismatched": gw_result.models_mismatched,
            "models_only_in_provider": gw_result.models_only_in_provider,
            "models_only_in_db": gw_result.models_only_in_db,
            "mismatches": mismatches_list,
            "unmatched_provider": unmatched_provider_list,
            "unmatched_db": unmatched_db_list,
            "matched_models": matched_list,
            "errors": gw_result.errors,
        }

    return result
