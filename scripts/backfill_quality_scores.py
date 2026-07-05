#!/usr/bin/env python3
"""
Backfill inferred quality priors into model_quality_scores.

Populates a deterministic `source='inferred'` prior for every canonical model
family that has NO curated (manual/benchmark) score, so `model_selector` becomes
quality-aware across the whole long-tail catalog with zero hand-curation. Keyed
by canonical_id, so every provider serving a family shares one prior and new
providers need no work.

Idempotent and safe to re-run: recomputes from live signals and NEVER overwrites
a canonical family that already has any manual/benchmark row.

Usage:
    python3 scripts/backfill_quality_scores.py            # all uncurated families
    python3 scripts/backfill_quality_scores.py --dry-run  # show plan, write nothing
    python3 scripts/backfill_quality_scores.py --stats    # source/coverage histogram
    python3 scripts/backfill_quality_scores.py --limit 50 # smoke test
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from src.config.supabase_config import get_supabase_client
from src.services.quality_inference import (
    QualitySignals,
    infer_quality,
    parse_param_billions,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_quality_scores")

_SELECT = "canonical_id, provider_model_id, model_name, is_reasoning, context_length"
_PAGE = 1000
_CURATED_SOURCES = ("manual", "benchmark")


def _iter_models(supabase):
    offset = 0
    while True:
        resp = (
            supabase.table("models")
            .select(_SELECT)
            .eq("is_active", True)
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


def _aggregate_families(supabase) -> dict[str, QualitySignals]:
    """
    Collapse provider rows into one QualitySignals per canonical_id.

    Reasoning flag = OR across providers; name = the variant with the largest
    parseable size (most informative); context = max seen.
    """
    families: dict[str, QualitySignals] = {}
    best_params: dict[str, float] = {}
    for page in _iter_models(supabase):
        for r in page:
            cid = r.get("canonical_id")
            if not cid:
                continue  # null canonical_id can't be keyed into quality scores
            # Parse size/variant from the id fields (they carry "70B"/"Coder");
            # canonical_id is the family key, provider_model_id is a fallback.
            name = cid or r.get("provider_model_id") or r.get("model_name")
            params = parse_param_billions(name)
            if params is None:
                params = parse_param_billions(r.get("provider_model_id"))
            params = params if params is not None else -1.0
            try:
                ctx = int(r.get("context_length") or 0) or None
            except (TypeError, ValueError):
                ctx = None

            prev = families.get(cid)
            if prev is None:
                families[cid] = QualitySignals(
                    name=name, is_reasoning=bool(r.get("is_reasoning")), context_length=ctx
                )
                best_params[cid] = params
            else:
                new_name = name if params > best_params[cid] else prev.name
                best_params[cid] = max(best_params[cid], params)
                new_ctx = max(prev.context_length or 0, ctx or 0) or None
                families[cid] = QualitySignals(
                    name=new_name,
                    is_reasoning=prev.is_reasoning or bool(r.get("is_reasoning")),
                    context_length=new_ctx,
                )
    return families


def _curated_canonical_ids(supabase) -> set[str]:
    """canonical_ids that already have a manual/benchmark row — leave untouched."""
    curated: set[str] = set()
    offset = 0
    while True:
        resp = (
            supabase.table("model_quality_scores")
            .select("model_id, source")
            .in_("source", list(_CURATED_SOURCES))
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        for r in rows:
            curated.add(r["model_id"])
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return curated


def backfill(limit: int | None = None, dry_run: bool = False) -> int:
    supabase = get_supabase_client()
    families = _aggregate_families(supabase)
    curated = _curated_canonical_ids(supabase)

    targets = [cid for cid in sorted(families) if cid not in curated]
    if limit is not None:
        targets = targets[:limit]

    logger.info(
        "%d families total, %d curated (skipped), %d to infer",
        len(families),
        len(curated),
        len(targets),
    )

    rows: list[dict] = []
    for cid in targets:
        for task, score in infer_quality(families[cid]).items():
            rows.append(
                {"model_id": cid, "task_type": task, "score": score, "source": "inferred"}
            )

    if dry_run:
        logger.info("[dry-run] would upsert %d rows (%d families)", len(rows), len(targets))
        for cid in targets[:10]:
            logger.info("  %s -> overall=%.1f", cid, infer_quality(families[cid])["unknown"])
        return 0

    written = 0
    for i in range(0, len(rows), _PAGE):
        chunk = rows[i : i + _PAGE]
        supabase.table("model_quality_scores").upsert(
            chunk, on_conflict="model_id,task_type"
        ).execute()
        written += len(chunk)
        logger.info("Upserted %d/%d rows", written, len(rows))

    logger.info("Done. Inferred %d task-scores across %d families.", written, len(targets))
    return written


def print_stats() -> None:
    supabase = get_supabase_client()
    by_source: Counter[str] = Counter()
    families: set[str] = set()
    offset = 0
    while True:
        resp = (
            supabase.table("model_quality_scores")
            .select("model_id, source")
            .range(offset, offset + _PAGE - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        for r in rows:
            by_source[r.get("source") or "unknown"] += 1
            families.add(r["model_id"])
        if len(rows) < _PAGE:
            break
        offset += _PAGE

    print(f"\nmodel_quality_scores: {sum(by_source.values())} rows, {len(families)} families")
    for source, count in by_source.most_common():
        print(f"  {source:<12} {count:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill inferred model_quality_scores")
    ap.add_argument("--limit", type=int, default=None, help="max families to process")
    ap.add_argument("--dry-run", action="store_true", help="show plan, write nothing")
    ap.add_argument("--stats", action="store_true", help="print source histogram and exit")
    args = ap.parse_args()

    if args.stats:
        print_stats()
        return 0

    backfill(limit=args.limit, dry_run=args.dry_run)
    if not args.dry_run:
        print_stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
