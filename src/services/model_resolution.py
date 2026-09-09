"""Resolve an incoming model id to a canonical catalog id.

Callers send vendor-native ids: the Anthropic SDK sends `claude-sonnet-4-6`,
not `anthropic/claude-sonnet-4-6`, and scaffolds generated against our
OpenAI-compatible surface do the same. Before this module a bare Anthropic id
reached the pricing gate unresolved and was rejected as an *operator* failure
(503, "contact support") while a bare OpenAI id happened to route -- the
behavior was inconsistent by vendor, and the worse status code went to the
more reasonable caller.

Resolution is deliberately conservative and fails closed: exact catalog hit,
then a curated `model_aliases` row, then a suffix match that must be UNIQUE.
Two candidates is a refusal, never a guess -- guessing here routes a user's
prompt to a model they did not ask for and bills them for it.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_FREE_SUFFIX = ":free"

# Vendors publish an undated alias alongside a dated snapshot
# (`claude-sonnet-4-5` and `claude-sonnet-4-5-20250929`). Our catalog
# carries the dated row; the undated id is what the vendor's own docs and
# SDK examples use, so it must resolve.
_DATE_SUFFIX = re.compile(r"^(.+)-\d{8}$")


@dataclass(frozen=True)
class ModelResolution:
    """Outcome of one resolution attempt.

    `matched_by` is one of "exact", "alias", "suffix", "undated" (resolved) or
    "ambiguous", "unresolved" (not resolved -- `canonical_id` is None).
    `candidates` is populated only for "ambiguous", so the caller can name
    them in the error it returns.
    """

    canonical_id: str | None
    matched_by: str
    candidates: tuple[str, ...] = field(default=())


_index_lock = threading.RLock()
_exact: set[str] | None = None
_by_suffix: dict[str, tuple[str, ...]] | None = None
_by_undated: dict[str, tuple[str, ...]] | None = None
_aliases: dict[str, str] | None = None


def _load_catalog_ids() -> list[str]:
    """Every canonical id in the deduped catalog. [] when the cache is cold."""
    from src.services.cache.model_catalog_cache import get_cached_unique_models

    rows = get_cached_unique_models() or []
    return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]


def _load_alias_map() -> dict[str, str]:
    from src.services.model_canonicalization import load_alias_map

    return load_alias_map()


def _build_index() -> None:
    global _exact, _by_suffix, _by_undated, _aliases
    ids = _load_catalog_ids()
    exact: set[str] = set()
    suffix: dict[str, list[str]] = {}
    undated: dict[str, list[str]] = {}
    for mid in ids:
        low = mid.lower()
        exact.add(low)
        bare = low.rsplit("/", 1)[-1]
        if bare != low:
            suffix.setdefault(bare, []).append(mid)
        m = _DATE_SUFFIX.match(low)
        if m:
            base = m.group(1)
            undated.setdefault(base, []).append(mid)
            bare_base = base.rsplit("/", 1)[-1]
            if bare_base != base:
                undated.setdefault(bare_base, []).append(mid)
    _exact = exact
    _by_suffix = {k: tuple(sorted(v)) for k, v in suffix.items()}
    _by_undated = {k: tuple(sorted(v)) for k, v in undated.items()}
    _aliases = _load_alias_map()


def _ensure_index() -> None:
    if _exact is None or _by_suffix is None or _by_undated is None or _aliases is None:
        with _index_lock:
            if _exact is None or _by_suffix is None or _by_undated is None or _aliases is None:
                _build_index()


def index_is_empty() -> bool:
    """True when the catalog index holds nothing to resolve against.

    A cold cache, or a Supabase the process cannot reach, must not be allowed
    to turn every request into "that model does not exist" — callers check
    this and fall through to the pre-resolution behavior instead.
    """
    _ensure_index()
    return not _exact


def invalidate_resolution_index() -> None:
    """Drop the index; rebuilt lazily on the next resolve. Call on catalog sync."""
    global _exact, _by_suffix, _by_undated, _aliases
    with _index_lock:
        _exact = None
        _by_suffix = None
        _by_undated = None
        _aliases = None


def resolve_catalog_model_id(model_id: str) -> ModelResolution:
    """Map `model_id` to a canonical catalog id, or explain why it can't."""
    if not model_id or not model_id.strip():
        return ModelResolution(None, "unresolved")

    raw = model_id.strip()
    free = raw.lower().endswith(_FREE_SUFFIX)
    core = raw[: -len(_FREE_SUFFIX)] if free else raw
    low = core.lower()

    def _finish(canonical: str, how: str) -> ModelResolution:
        return ModelResolution(canonical + (_FREE_SUFFIX if free else ""), how)

    _ensure_index()
    exact = _exact or set()
    by_suffix = _by_suffix or {}
    by_undated = _by_undated or {}
    aliases = _aliases or {}

    if low in exact:
        return _finish(low, "exact")

    aliased = aliases.get(low)
    if aliased:
        return _finish(aliased, "alias")

    candidates = by_suffix.get(low, ())
    if len(candidates) == 1:
        logger.info("[MODEL_RESOLVE] '%s' -> '%s' (unique suffix)", raw, candidates[0])
        return _finish(candidates[0], "suffix")
    if len(candidates) > 1:
        logger.info("[MODEL_RESOLVE] '%s' is ambiguous across %s", raw, list(candidates))
        return ModelResolution(None, "ambiguous", candidates)

    # Undated vendor alias -> its dated snapshot. Last, so an exact id, a
    # curated alias and a unique suffix all win first. Still fails closed:
    # two snapshots is a refusal, because picking "the newest" would move a
    # caller between models on a catalog sync without them asking.
    dated = by_undated.get(low, ())
    if len(dated) == 1:
        logger.info("[MODEL_RESOLVE] '%s' -> '%s' (undated alias)", raw, dated[0])
        return _finish(dated[0], "undated")
    if len(dated) > 1:
        logger.info("[MODEL_RESOLVE] '%s' is ambiguous across snapshots %s", raw, list(dated))
        return ModelResolution(None, "ambiguous", dated)

    return ModelResolution(None, "unresolved")
