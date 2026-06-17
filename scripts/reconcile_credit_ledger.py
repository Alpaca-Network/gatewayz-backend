#!/usr/bin/env python3
"""Reconcile the Phase 3 shadow credit ledger against live billing.

Item 2 of the Gatewayz One shadow→reconcile→cutover chain. Pulls
``public.credit_ledger`` (shadow settlements) and ``public.usage_records`` (what
the live system recorded it charged) over a time window, then reports whether the
ledger is internally balanced and agrees with live billing per user and overall.

The reconciliation math lives in (and is unit-tested via)
``src.services.billing.ledger_reconciliation`` — this script is only the I/O +
rendering shell.

Usage:
    python scripts/reconcile_credit_ledger.py                 # last 7 days
    python scripts/reconcile_credit_ledger.py --since 3d       # last 3 days
    python scripts/reconcile_credit_ledger.py --since 2026-06-17T00:00:00Z
    python scripts/reconcile_credit_ledger.py --tolerance 0.05 --json

Exit code: 0 when the ledger reconciles (report.ok), 1 when there is drift or an
unbalanced ref (so it can gate a cutover check in CI/cron).

Requires SUPABASE_URL + a service-role key in the environment (the ledger table
is RLS-locked to service_role). Run against the same project the gateway deploys
against (production → ynleroehyrmaafkgjgmr).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_config import get_supabase_client  # noqa: E402
from src.services.billing.ledger_reconciliation import (  # noqa: E402
    DEFAULT_MIN_COST,
    DEFAULT_TOLERANCE,
    reconcile_rows,
)

_PAGE = 1000
_REL_RE = re.compile(r"^(\d+)\s*([dh])$")  # "3d", "12h"


def _parse_when(value: str, *, now: datetime) -> str:
    """Parse an absolute ISO timestamp or a relative '<N>d'/'<N>h' into ISO-UTC."""
    m = _REL_RE.match(value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
        return (now - delta).isoformat()
    # Absolute: normalize a trailing 'Z' to +00:00 so fromisoformat accepts it.
    iso = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(iso).astimezone(UTC).isoformat()


def _fetch_all(table: str, time_col: str, since: str, until: str) -> list[dict]:
    """Page through every row of ``table`` in [since, until) (Supabase caps at 1000)."""
    client = get_supabase_client()
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table(table)
            .select("*")
            .gte(time_col, since)
            .lt(time_col, until)
            .order(time_col)
            .range(start, start + _PAGE - 1)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        start += _PAGE


def _admin_user_ids() -> frozenset:
    """User ids the shadow path skips (tier == 'admin'). Empty set on any failure."""
    try:
        client = get_supabase_client()
        resp = client.table("users").select("id").eq("tier", "admin").execute()
        return frozenset(r["id"] for r in (getattr(resp, "data", None) or []) if r.get("id") is not None)
    except Exception as e:  # column/table differences shouldn't break reconciliation
        print(f"warning: could not resolve admin user ids ({e}); not excluding any", file=sys.stderr)
        return frozenset()


def _render(report, since: str, until: str, admin_count: int) -> str:
    lines = []
    status = "OK ✅" if report.ok else "DRIFT ❌"
    lines.append(f"Credit-ledger reconciliation — {status}")
    lines.append(f"  window:        {since}  →  {until}")
    lines.append(f"  ledger refs:   {report.ledger_ref_count}")
    lines.append(f"  usage rows:    {report.usage_row_count} (admins excluded: {admin_count})")
    lines.append(f"  ledger revenue:{report.total_ledger_revenue:>18}")
    lines.append(f"  live usage:    {report.total_usage_cost:>18}")
    lines.append(f"  total drift:   {report.total_drift:>18}  (tolerance ±{report.tolerance})")
    if report.unbalanced_refs:
        lines.append(f"  UNBALANCED refs ({len(report.unbalanced_refs)}): {report.unbalanced_refs[:20]}")
    offenders = [u for u in report.per_user if not u.within_tolerance]
    if offenders:
        lines.append(f"  per-user drift over tolerance ({len(offenders)}):")
        for u in offenders[:25]:
            lines.append(
                f"    user {u.user_id}: ledger {u.ledger_revenue} vs usage {u.usage_cost} "
                f"→ drift {u.drift}"
            )
    elif report.ok:
        lines.append("  every user within tolerance; ledger balanced.")
    return "\n".join(lines)


def _to_jsonable(report, since, until, admin_count) -> dict:
    return {
        "ok": report.ok,
        "window": {"since": since, "until": until},
        "ledger_ref_count": report.ledger_ref_count,
        "usage_row_count": report.usage_row_count,
        "admin_excluded": admin_count,
        "total_ledger_revenue": str(report.total_ledger_revenue),
        "total_usage_cost": str(report.total_usage_cost),
        "total_drift": str(report.total_drift),
        "tolerance": str(report.tolerance),
        "unbalanced_refs": report.unbalanced_refs,
        "users_over_tolerance": [
            {
                "user_id": u.user_id,
                "ledger_revenue": str(u.ledger_revenue),
                "usage_cost": str(u.usage_cost),
                "drift": str(u.drift),
            }
            for u in report.per_user
            if not u.within_tolerance
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="7d", help="ISO timestamp or relative '<N>d'/'<N>h' (default 7d)")
    parser.add_argument("--until", default=None, help="ISO timestamp (default: now)")
    parser.add_argument("--tolerance", type=Decimal, default=DEFAULT_TOLERANCE, help="acceptable absolute drift (USD)")
    parser.add_argument("--min-cost", type=Decimal, default=DEFAULT_MIN_COST, help="usage rows below this are excluded")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a text report")
    args = parser.parse_args()

    now = datetime.now(UTC)
    since = _parse_when(args.since, now=now)
    until = _parse_when(args.until, now=now) if args.until else now.isoformat()

    ledger_rows = _fetch_all("credit_ledger", "created_at", since, until)
    usage_rows = _fetch_all("usage_records", "timestamp", since, until)
    admins = _admin_user_ids()

    report = reconcile_rows(
        ledger_rows,
        usage_rows,
        min_cost=args.min_cost,
        exclude_user_ids=admins,
        tolerance=args.tolerance,
    )

    if args.json:
        print(json.dumps(_to_jsonable(report, since, until, len(admins)), indent=2))
    else:
        print(_render(report, since, until, len(admins)))

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
