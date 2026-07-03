#!/usr/bin/env python3
"""Shadow report for the catalog quality gate — writes NOTHING.

Fetches a provider's live catalog and runs the quality gate over it, reporting
how many models would be kept vs dropped and why. Use this to validate the gate
(and decide filter-vs-cut for a host like Featherless) before enabling it.

Usage:
    python scripts/model_quality_shadow_report.py                       # featherless
    python scripts/model_quality_shadow_report.py --provider together
    python scripts/model_quality_shadow_report.py --provider featherless --show 30
    python scripts/model_quality_shadow_report.py --provider novita --json

Needs whatever API key that provider's fetch function requires (e.g.
FEATHERLESS_API_KEY) in the environment / .env.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.model_quality_gate import assess  # noqa: E402


def _model_id(m: dict) -> str:
    return str(m.get("id") or m.get("model_id") or m.get("name") or m.get("slug") or "?")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="featherless", help="provider slug to fetch")
    parser.add_argument("--show", type=int, default=15, help="sample N dropped models per reason")
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args()

    from src.services.model_catalog_sync import PROVIDER_FETCH_FUNCTIONS

    fetch_fn = PROVIDER_FETCH_FUNCTIONS.get(args.provider)
    if fetch_fn is None:
        sys.exit(
            f"No fetch function for '{args.provider}'. "
            f"Known: {', '.join(sorted(PROVIDER_FETCH_FUNCTIONS))}"
        )

    print(f"Fetching '{args.provider}' catalog…", file=sys.stderr)
    models = fetch_fn() or []
    if not models:
        sys.exit(f"'{args.provider}' returned 0 models (missing API key, or empty catalog).")

    # normalized models may be dicts or objects; coerce to dicts of the fields we read.
    def _as_dict(m):
        if isinstance(m, dict):
            return m
        return {
            k: getattr(m, k, None) for k in ("id", "model_id", "name", "slug", "canonical_slug")
        }

    kept, dropped = [], []
    reasons: collections.Counter[str] = collections.Counter()
    samples: dict[str, list[str]] = collections.defaultdict(list)

    for raw in models:
        m = _as_dict(raw)
        v = assess(m, args.provider)
        if v.keep:
            kept.append(_model_id(m))
        else:
            dropped.append(_model_id(m))
            reasons[v.reason] += 1
            if len(samples[v.reason]) < args.show:
                samples[v.reason].append(_model_id(m))

    total = len(models)
    summary = {
        "provider": args.provider,
        "total": total,
        "kept": len(kept),
        "dropped": len(dropped),
        "drop_rate_pct": round(100 * len(dropped) / max(total, 1), 1),
        "reasons": dict(reasons.most_common()),
    }

    if args.json:
        print(json.dumps({"summary": summary, "dropped_samples": samples}, indent=2))
        return 0

    print("=" * 68)
    print(f"QUALITY GATE SHADOW REPORT — {args.provider}")
    print("=" * 68)
    print(f"Total fetched : {total}")
    print(f"Would KEEP    : {len(kept)}  ({100 - summary['drop_rate_pct']:.1f}%)")
    print(f"Would DROP    : {len(dropped)}  ({summary['drop_rate_pct']:.1f}%)")
    print("\nDrop reasons:")
    for reason, n in reasons.most_common():
        print(f"  {reason:<22} {n:>6}")
        for sid in samples[reason]:
            print(f"      · {sid}")
    print("\n(No changes written — this is a dry run.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
