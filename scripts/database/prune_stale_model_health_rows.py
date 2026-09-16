#!/usr/bin/env python3
"""
Disable model_health_tracking rows that name no model in the served catalog.

WHY
---
model_health_tracking outlives the catalog and nothing prunes it. In production
on 2026-09-15 there were 123 enabled tracking rows against 66 catalog models,
and 30 of them answered "Model not found" on every probe: real requests spent to
manufacture failures for models we do not sell, then republished on the public
status page.

The monitor now prunes these automatically once an hour
(IntelligentHealthMonitor.prune_orphaned_tracking_rows, wired into
_tier_update_loop and health-service/main.py, gated by
Config.HEALTH_PROBE_PRUNE_ORPHANS). This script is the one-off / manual path for
clearing the existing backlog immediately, or for auditing what would be pruned
without waiting for the loop.

Rows are marked is_enabled = false, never deleted: their history stays auditable,
and a model that returns to the catalog is picked up again by the normal
tracking path.

Usage:
    # Report only — makes no writes. Run this first.
    python scripts/database/prune_stale_model_health_rows.py --dry-run

    # Apply.
    python scripts/database/prune_stale_model_health_rows.py

Requires SUPABASE_URL / SUPABASE_KEY in the environment, the same as the app.
Exit code 0 on success, 1 on failure.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger(__name__)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be disabled without writing anything.",
    )
    args = parser.parse_args()

    configure_logging()

    from src.services.monitoring.intelligent_health_monitor import intelligent_health_monitor

    summary = await intelligent_health_monitor.prune_orphaned_tracking_rows(dry_run=args.dry_run)

    logger.info("Prune summary: %s", summary)

    if summary.get("error"):
        logger.error("Prune failed: %s", summary["error"])
        return 1
    if summary.get("skipped_reason"):
        # An empty catalog scope means the lookup failed, not that we sell
        # nothing. Disabling every row on that reading would stop all
        # monitoring, so the prune refuses rather than guessing.
        logger.error("Prune skipped: %s", summary["skipped_reason"])
        return 1

    if args.dry_run:
        logger.info(
            "DRY RUN: %d of %d enabled rows match no catalog model. Re-run without "
            "--dry-run to disable them.",
            summary.get("orphaned", 0),
            summary.get("scanned", 0),
        )
    else:
        logger.info(
            "Disabled %d of %d enabled rows (%d orphaned).",
            summary.get("disabled", 0),
            summary.get("scanned", 0),
            summary.get("orphaned", 0),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
