#!/usr/bin/env python3
"""Project public.models → public.model_provider_offers (Gatewayz One Phase 1).

Fills the registry projection the Phase 2 smart router scores over, from the live
catalog. The transform + I/O live in (and are unit-tested via)
``src.services.model_offers_projection``; this is just the CLI shell. The same
``refresh_offers_projection`` runs automatically after each scheduled model sync /
price refresh (see src/services/scheduled_sync.py).

Usage:
    railway run -- python scripts/project_model_provider_offers.py --dry-run
    railway run -- python scripts/project_model_provider_offers.py            # full upsert
    railway run -- python scripts/project_model_provider_offers.py --only-multi
    railway run -- python scripts/project_model_provider_offers.py --json

Run against the project the gateway deploys against (production → ynleroehyrmaafkgjgmr);
the table is RLS-locked so a service-role key is required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.model_offers_projection import refresh_offers_projection  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="build + report, do not write")
    parser.add_argument("--only-multi", action="store_true", help="only models served by >1 gateway")
    parser.add_argument("--limit", type=int, default=None, help="cap models scanned (testing)")
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args()

    result = refresh_offers_projection(
        only_multi=args.only_multi, dry_run=args.dry_run, limit=args.limit
    )
    summary = result["summary"]
    sample = sorted(result["offers"], key=lambda o: o["canonical_id"])[:5]

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
