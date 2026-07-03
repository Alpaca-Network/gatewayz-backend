#!/usr/bin/env python3
"""
Backfill model.categories for the existing catalog.

Idempotent: recomputes tags from live source data (model row + model_pricing +
model_quality_scores) each run, so it is safe to re-run after tuning thresholds
in the category_rules table.

Usage:
    python3 scripts/backfill_model_categories.py            # all active models
    python3 scripts/backfill_model_categories.py --limit 50 # smoke test
    python3 scripts/backfill_model_categories.py --stats    # print tag histogram

Spec: docs/superpowers/specs/2026-07-04-model-categorization-design.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from src.config.supabase_config import get_supabase_client
from src.db.models_catalog_db import _sync_categories

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_model_categories")

# Columns the categorizer needs off the models row. (`architecture` is a
# provider-fetch-only field, not a column on the models table — omit it.)
_SELECT = "id, canonical_id, context_length, latency_tier, is_reasoning, is_free, modality"
_PAGE = 500


def _iter_models(supabase):
    """Yield pages of active (non-deprecated) model rows."""
    offset = 0
    while True:
        resp = (
            supabase.table("models")
            .select(_SELECT)
            .is_("deprecated_at", "null")
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return
        yield rows
        if len(rows) < _PAGE:
            return
        offset += _PAGE


def backfill(limit: int | None = None) -> int:
    supabase = get_supabase_client()
    total = 0
    for page in _iter_models(supabase):
        if limit is not None and total + len(page) > limit:
            page = page[: limit - total]
        # _sync_categories recomputes and writes categories for these rows.
        _sync_categories(supabase, page)
        total += len(page)
        logger.info("Backfilled %d models so far", total)
        if limit is not None and total >= limit:
            break
    logger.info("Done. Categorized %d models.", total)
    return total


def print_stats() -> None:
    """Histogram of tag frequency across the catalog — use to tune thresholds."""
    supabase = get_supabase_client()
    counter: Counter[str] = Counter()
    n = 0
    offset = 0
    while True:
        resp = (
            supabase.table("models")
            .select("categories")
            .is_("deprecated_at", "null")
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        for r in rows:
            n += 1
            for tag in r.get("categories") or []:
                counter[tag] += 1
        if len(rows) < _PAGE:
            break
        offset += _PAGE

    print(f"\nTag distribution over {n} active models:")
    for tag, count in counter.most_common():
        pct = (count / n * 100) if n else 0
        print(f"  {tag:<14} {count:>6}  ({pct:5.1f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill model.categories")
    ap.add_argument("--limit", type=int, default=None, help="max models to process")
    ap.add_argument("--stats", action="store_true", help="print tag histogram and exit")
    args = ap.parse_args()

    if args.stats:
        print_stats()
        return 0

    backfill(limit=args.limit)
    print_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
