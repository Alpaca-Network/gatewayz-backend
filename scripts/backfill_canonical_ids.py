#!/usr/bin/env python3
"""
Backfill models.canonical_id from provider_model_id via the alias registry.

Why: model_quality_scores is keyed by canonical id (e.g. "openai/gpt-4o"), and
the categorizer joins quality on models.canonical_id. That column was added by
the Phase-1 registry migration but never populated, so quality-derived category
tags (smartest/coding/flagship/mid/balanced) never fire. This resolves each
model's canonical id best-effort using apply_model_alias() (the same resolver
the request path uses), so the join can succeed for known models.

Best-effort: models not in the alias table keep their provider-native
`vendor/model` id as canonical (already the right shape for most direct-supply
models). Idempotent — safe to re-run.

Usage:
    python3 scripts/backfill_canonical_ids.py            # all active, null canonical
    python3 scripts/backfill_canonical_ids.py --all      # recompute even if set
    python3 scripts/backfill_canonical_ids.py --limit 50 # smoke test
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config.supabase_config import get_supabase_client
from src.services.model_transformations import apply_model_alias

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_canonical_ids")

_PAGE = 500


def _canonical_for(row: dict) -> str | None:
    """Best-effort canonical id for a model row."""
    pmid = row.get("provider_model_id")
    if pmid:
        resolved = apply_model_alias(pmid)
        if resolved:
            return resolved
    # Fall back to alias resolution on the display name (rarely needed).
    name = row.get("model_name")
    return apply_model_alias(name) if name else None


def _snapshot_targets(supabase, recompute_all: bool) -> list[dict]:
    """
    Read all target rows FIRST via keyset pagination on id.

    Keyset (id > last_id) is stable even though we later write canonical_id —
    unlike offset pagination on the `canonical_id IS null` filter, which the
    update itself invalidates (rows drop out mid-scan → skipped).
    """
    rows: list[dict] = []
    last_id = 0
    while True:
        query = (
            supabase.table("models")
            .select("id, model_name, provider_model_id, canonical_id")
            .is_("deprecated_at", "null")
            .eq("is_active", True)
            .gt("id", last_id)
            .order("id")
            .limit(_PAGE)
        )
        if not recompute_all:
            query = query.is_("canonical_id", "null")
        page = query.execute().data or []
        if not page:
            break
        rows.extend(page)
        last_id = page[-1]["id"]
        if len(page) < _PAGE:
            break
    return rows


def backfill(recompute_all: bool = False, limit: int | None = None) -> tuple[int, int]:
    supabase = get_supabase_client()
    targets = _snapshot_targets(supabase, recompute_all)
    if limit is not None:
        targets = targets[:limit]
    logger.info("Resolving canonical_id for %d models", len(targets))

    updated = 0
    resolved_via_alias = 0
    failed = 0
    for row in targets:
        canonical = _canonical_for(row)
        if not canonical:
            continue
        try:
            supabase.table("models").update({"canonical_id": canonical}).eq(
                "id", row["id"]
            ).execute()
            updated += 1
            if canonical != row.get("provider_model_id"):
                resolved_via_alias += 1
        except Exception as e:  # noqa: BLE001 — isolate per-row failures
            failed += 1
            logger.error("canonical_id update failed for model %s: %s", row["id"], e)
        if updated % 200 == 0 and updated:
            logger.info("Backfilled canonical_id for %d models so far", updated)

    logger.info(
        "Done. Set canonical_id on %d models (%d via alias, %d failed).",
        updated,
        resolved_via_alias,
        failed,
    )
    return updated, resolved_via_alias


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill models.canonical_id")
    ap.add_argument("--all", action="store_true", help="recompute even if already set")
    ap.add_argument("--limit", type=int, default=None, help="max models to process")
    args = ap.parse_args()
    backfill(recompute_all=args.all, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
