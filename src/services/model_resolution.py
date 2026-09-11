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
_aliases: dict[str, str] | None = None


def _load_catalog_ids() -> list[str]:
    """Every model id this gateway can be asked for. [] when caches are cold.

    Two sources, and the first one is the point (#2304):

    - ``get_cached_models("all")`` is the FLAT catalog — exactly what
      ``GET /v1/models`` serves, i.e. what we advertise and what partners
      build against. If we list a model, we have to accept its id; a catalog
      that advertises what the resolver rejects is a broken promise.
    - ``get_cached_unique_models()`` is the DEDUPED catalog, the only source
      this index used until 2026-09-11. Measured in production that day, it
      carried none of the ids ``/v1/models`` advertises: every fully-qualified
      id resolved to nothing and all working bare-id resolution ran through
      the curated ``model_aliases`` table. Kept in the union rather than
      dropped, because nothing here should depend on the two agreeing — this
      function's job is to be the superset.

    Each source is guarded separately: one being cold or unavailable must
    never take the other down with it.
    """
    ids: list[str] = []

    def _collect(rows) -> None:
        for r in rows or []:
            if isinstance(r, dict) and r.get("id"):
                ids.append(str(r["id"]))

    try:
        from src.services.models import get_cached_models

        _collect(get_cached_models("all"))
    except Exception as e:  # noqa: BLE001 - a cold flat catalog is not fatal
        logger.warning("[MODEL_RESOLVE] flat catalog unavailable for index: %s", e)

    try:
        from src.services.cache.model_catalog_cache import get_cached_unique_models

        _collect(get_cached_unique_models())
    except Exception as e:  # noqa: BLE001
        logger.warning("[MODEL_RESOLVE] unique catalog unavailable for index: %s", e)

    return ids


def _load_alias_map() -> dict[str, str]:
    from src.services.model_canonicalization import load_alias_map

    return load_alias_map()


def _build_index() -> None:
    global _exact, _by_suffix, _aliases
    ids = _load_catalog_ids()
    exact: set[str] = set()
    suffix: dict[str, list[str]] = {}
    for mid in ids:
        low = mid.lower()
        exact.add(low)
        bare = low.rsplit("/", 1)[-1]
        if bare != low:
            suffix.setdefault(bare, []).append(mid)
    _exact = exact
    _by_suffix = {k: tuple(sorted(v)) for k, v in suffix.items()}
    _aliases = _load_alias_map()
    logger.info(
        "[MODEL_RESOLVE] index built: %d ids, %d bare suffixes, %d curated aliases",
        len(_exact),
        len(_by_suffix),
        len(_aliases),
    )


def _ensure_index() -> None:
    if _exact is None or _by_suffix is None or _aliases is None:
        with _index_lock:
            if _exact is None or _by_suffix is None or _aliases is None:
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
    global _exact, _by_suffix, _aliases
    with _index_lock:
        _exact = None
        _by_suffix = None
        _aliases = None


def _known_canonical_ids(exact: set[str], aliases: dict[str, str]) -> set[str]:
    """Every canonical id this process can actually reach.

    The exact-id set alone is NOT that. Measured in production 2026-09-10 via
    /health/model-resolution: `anthropic/claude-sonnet-4-6`,
    `openai/gpt-4o-mini` and `anthropic/claude-haiku-4-5-20251001` all report
    `unresolved`, while every bare form reports `alias`. The catalog index
    built from `get_cached_unique_models()` contains none of the prefixed ids
    that GET /v1/models advertises, so in practice the curated `model_aliases`
    table is what maps bare names onto real models.

    Its VALUES are therefore canonical ids the gateway serves, and any scan
    over "the ids we know" has to include them or it scans the wrong universe.
    (That the two disagree at all is a separate defect — see #2304.)
    """
    return exact | {str(v).lower() for v in aliases.values() if v}


def _undated_snapshots(low: str, known: set[str]) -> tuple[str, ...]:
    """Ids in `known` that `low` denotes as an undated vendor alias.

    Derived on each call rather than kept as a parallel index. The catalog is
    a few dozen ids, so the scan is free, and a derived answer cannot drift
    out of step with the set it is derived from — which a second index built
    in the same loop demonstrably can.
    """
    out = []
    for mid in known:
        m = _DATE_SUFFIX.match(mid)
        if not m:
            continue
        base = m.group(1)
        if base == low or base.rsplit("/", 1)[-1] == low:
            out.append(mid)
    return tuple(sorted(out))


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
    dated = _undated_snapshots(low, _known_canonical_ids(exact, aliases))
    if len(dated) == 1:
        logger.info("[MODEL_RESOLVE] '%s' -> '%s' (undated alias)", raw, dated[0])
        return _finish(dated[0], "undated")
    if len(dated) > 1:
        logger.info("[MODEL_RESOLVE] '%s' is ambiguous across snapshots %s", raw, list(dated))
        return ModelResolution(None, "ambiguous", dated)

    return ModelResolution(None, "unresolved")
