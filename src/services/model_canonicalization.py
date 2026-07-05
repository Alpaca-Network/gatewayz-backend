"""Model canonicalization for COST ROUTING — group the same logical model across
providers so the smart router can compare their prices.

Scope is deliberately narrow: this produces a *grouping key* for the
``model_provider_offers`` projection and the routing bridge ONLY. It does NOT
touch ``models.canonical_id`` — that column is consumed by the categorization /
``model_quality_scores`` join and keeps its curated ``vendor/model`` shape. Mixing
the two would break quality-prior lookups.

Conservative by design: cosmetic differences (casing, separators, known re-host
prefixes) collapse, but different orgs never merge automatically — only a curated
``model_aliases`` row may cross an org boundary. This keeps false merges (routing a
user to the wrong model) off the automatic path. The provider-native id is always
preserved on each offer (``native_id``) and is what actually gets dispatched.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Gateways that re-host other providers' models under a prefix. Stripping the
# prefix lets the re-hosted offer group with the origin for cost comparison.
_REHOST_PREFIXES = ("near/",)

_SEP_RE = re.compile(r"[-_.]")


def _strip_rehost_prefix(mid: str) -> str:
    for p in _REHOST_PREFIXES:
        if mid.startswith(p):
            return mid[len(p) :]
    return mid


def normalization_key(provider_model_id: str) -> str:
    """Deterministic conservative grouping key for a native model id.

    Lowercases, strips a known re-host prefix, then unifies separators within the
    org and name segments while KEEPING the org/name split so unrelated orgs do
    not collide. Returns "" for falsy input.
    """
    if not provider_model_id:
        return ""
    s = _strip_rehost_prefix(provider_model_id.strip().lower())
    parts = s.split("/")
    name = _SEP_RE.sub("", parts[-1])
    org = _SEP_RE.sub("", parts[-2]) if len(parts) > 1 else ""
    return f"{org}/{name}" if org else name


def offer_group_key(provider_model_id: str, alias_map: dict[str, str] | None = None) -> str:
    """Cost-routing grouping key for one provider's native model id.

    Applies a curated alias first (``alias_map`` maps a lowercased native id → a
    canonical id, letting a curator merge groups the deterministic key keeps
    apart — e.g. ``z-ai`` vs ``zai-org``), then the deterministic normalization
    key. Pure; both the offers projection and the routing bridge call this so the
    projected group and the request-time lookup always agree.
    """
    if not provider_model_id:
        return ""
    resolved = provider_model_id
    if alias_map:
        resolved = alias_map.get(provider_model_id.strip().lower(), provider_model_id)
    return normalization_key(resolved)


def load_alias_map() -> dict[str, str]:
    """Load ``model_aliases`` as ``{lowercased alias: canonical_id}``. {} on failure."""
    try:
        from src.db.model_mappings import get_all_model_aliases

        rows = get_all_model_aliases() or []
        return {
            str(r["alias"]).strip().lower(): r["canonical_id"]
            for r in rows
            if r.get("alias") and r.get("canonical_id")
        }
    except Exception as e:  # never break projection/routing on alias-load failure
        logger.warning("model alias map load failed (using empty map): %s", e)
        return {}


def suspicious_merges(
    model_rows: list[dict], alias_map: dict[str, str] | None = None
) -> list[dict]:
    """Flag offer groups whose members disagree on a hard attribute.

    Different context windows (>2x) within one group usually means the key
    over-merged two distinct models — a curator should add a splitting/merging
    alias. Returns ``[{group_key, reason, members}]``. QA aid, not on the hot path.
    """
    groups: dict[str, list[dict]] = {}
    for r in model_rows:
        pid = r.get("provider_model_id")
        if not pid:
            continue
        groups.setdefault(offer_group_key(pid, alias_map), []).append(r)

    flags: list[dict] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        ctxs = [int(m.get("context_length") or 0) for m in members if m.get("context_length")]
        if ctxs and max(ctxs) > 2 * min(ctxs):
            flags.append(
                {
                    "group_key": key,
                    "reason": "context_length_mismatch",
                    "members": [m.get("provider_model_id") for m in members],
                }
            )
    return flags
