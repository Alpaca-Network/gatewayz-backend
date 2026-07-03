#!/usr/bin/env python3
"""Deactivate the 6 removed aggregator/proxy providers in the DB.

Mirrors supabase/migrations/20260704000000_deactivate_aggregator_providers.sql
for environments where you'd rather run it via the Supabase client than psql.

Sets providers.is_active = false (and their models) for:
    vercel-ai-gateway, onerouter, aihubmix, anannas, helicone, notdiamond

Rows are NOT deleted (historical FKs stay intact); reversible by flipping
is_active back to true.

Usage:
    # Dry run — report what WOULD change, write nothing (do this first):
    python scripts/deactivate_aggregator_providers.py --dry-run

    # Apply. Point it at the PRODUCTION project — requires a service-role key,
    # since the providers/models tables are RLS-locked:
    SUPABASE_URL=https://<prod-ref>.supabase.co \
    SUPABASE_KEY=<service-role-key> \
        python scripts/deactivate_aggregator_providers.py --apply

This script deliberately has NO default target — you must pass the env vars,
so you can't accidentally run it against the wrong database.
"""

from __future__ import annotations

import argparse
import os
import sys

TARGET_SLUGS = [
    "vercel-ai-gateway",
    "onerouter",
    "aihubmix",
    "anannas",
    "helicone",
    "notdiamond",
]


def _client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit(
            "ERROR: set SUPABASE_URL and SUPABASE_KEY (service-role) env vars "
            "pointing at the target project."
        )
    try:
        from supabase import create_client
    except ImportError:
        sys.exit("ERROR: `supabase` package not installed (pip install supabase).")
    return create_client(url, key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    group.add_argument("--apply", action="store_true", help="perform the deactivation")
    args = parser.parse_args()

    sb = _client()

    # Resolve the target provider rows.
    providers = (
        sb.table("providers")
        .select("id,slug,name,is_active")
        .in_("slug", TARGET_SLUGS)
        .execute()
        .data
        or []
    )
    if not providers:
        print("No matching providers found — nothing to do (already removed?).")
        return 0

    provider_ids = [p["id"] for p in providers]

    # Count models that would be affected.
    models_resp = (
        sb.table("models")
        .select("id", count="exact")
        .in_("provider_id", provider_ids)
        .eq("is_active", True)
        .execute()
    )
    active_models = models_resp.count or 0

    print(f"Target project: {os.environ['SUPABASE_URL']}")
    print(f"Providers matched: {len(providers)}")
    for p in providers:
        print(f"  - {p['slug']:<20} (active={p['is_active']}) {p['name']}")
    print(f"Active models under these providers: {active_models}")

    if args.dry_run:
        print("\n[DRY RUN] No changes written.")
        return 0

    # Apply.
    sb.table("providers").update({"is_active": False}).in_("slug", TARGET_SLUGS).execute()
    sb.table("models").update({"is_active": False}).in_("provider_id", provider_ids).execute()
    print(f"\n[APPLIED] Deactivated {len(providers)} providers and {active_models} models.")
    print("Restart the gateway (or wait 5 min for the registry cache TTL) to reflect changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
