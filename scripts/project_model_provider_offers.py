#!/usr/bin/env python3
"""Project public.models → public.model_provider_offers (Gatewayz One Phase 1).

Fills the registry projection the Phase 2 smart router scores over, from the live
catalog. The transform is pure + unit-tested in
``src.services.model_offers_projection``; this is the fetch + upsert shell.

Usage:
    railway run -- python scripts/project_model_provider_offers.py --dry-run
    railway run -- python scripts/project_model_provider_offers.py            # full upsert
    railway run -- python scripts/project_model_provider_offers.py --only-multi  # only models served by >1 gateway
    railway run -- python scripts/project_model_provider_offers.py --json

Idempotent: upserts on the (canonical_id, provider_slug) unique key. Run against
the project the gateway deploys against (production → ynleroehyrmaafkgjgmr); the
table is RLS-locked so a service-role key is required.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.model_offers_projection import build_offer_rows, offer_summary  # noqa: E402

_PAGE = 1000
_UPSERT_BATCH = 500
_MODEL_COLS = (
    "id,provider_id,provider_model_id,pricing_original_prompt,"
    "success_rate,average_response_time_ms,is_active,modality"
)


def _client():
    from src.config.supabase_config import get_supabase_client

    return get_supabase_client()


def _providers_by_id() -> dict:
    resp = _client().table("providers").select("id,slug,name").execute()
    out: dict = {}
    for p in getattr(resp, "data", None) or []:
        out[p["id"]] = p
        out[str(p["id"])] = p  # tolerate int/str provider_id
    return out


def _fetch_active_models(limit: int | None) -> list[dict]:
    client = _client()
    rows: list[dict] = []
    start = 0
    while True:
        end = start + _PAGE - 1
        resp = (
            client.table("models")
            .select(_MODEL_COLS)
            .eq("is_active", True)
            .range(start, end)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if limit and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < _PAGE:
            return rows
        start += _PAGE


def _filter_multi(offers: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for o in offers:
        counts[o["canonical_id"]] = counts.get(o["canonical_id"], 0) + 1
    return [o for o in offers if counts[o["canonical_id"]] > 1]


def _upsert(offers: list[dict]) -> int:
    client = _client()
    stamp = datetime.now(UTC).isoformat()
    written = 0
    for i in range(0, len(offers), _UPSERT_BATCH):
        batch = [dict(o, updated_at=stamp) for o in offers[i : i + _UPSERT_BATCH]]
        client.table("model_provider_offers").upsert(
            batch, on_conflict="canonical_id,provider_slug"
        ).execute()
        written += len(batch)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="build + report, do not write")
    parser.add_argument("--only-multi", action="store_true", help="only models served by >1 gateway")
    parser.add_argument("--limit", type=int, default=None, help="cap models scanned (testing)")
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args()

    providers = _providers_by_id()
    models = _fetch_active_models(args.limit)
    offers = build_offer_rows(models, providers)
    if args.only_multi:
        offers = _filter_multi(offers)

    summary = offer_summary(offers)
    summary["models_scanned"] = len(models)
    summary["dry_run"] = args.dry_run
    summary["only_multi"] = args.only_multi

    if not args.dry_run:
        summary["rows_written"] = _upsert(offers)

    sample = sorted(offers, key=lambda o: o["canonical_id"])[:5]

    if args.json:
        print(json.dumps({"summary": summary, "sample": sample}, indent=2))
    else:
        print("model_provider_offers projection")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print("  sample offers:")
        for o in sample:
            print(f"    {o['canonical_id']}  via {o['provider_slug']}  ${o['upstream_cost']}/1k")
    return 0


if __name__ == "__main__":
    sys.exit(main())
