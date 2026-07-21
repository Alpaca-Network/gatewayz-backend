"""
Scheduled Model Sync Service (Phase 3 - Issue #996)

Provides background job to sync models from provider APIs to database
at regular intervals, keeping the database fresh for DB-first architecture.

Features:
- APScheduler-based job scheduling
- Configurable sync interval
- Error handling with logging
- Graceful shutdown
- Health monitoring integration
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config.config import Config

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None

# Separate scheduler instance for the lightweight price-only refresh.
# Kept independent from _scheduler so the (currently disabled) full sync and the
# price refresh can be enabled/disabled and started/stopped independently.
_price_scheduler: AsyncIOScheduler | None = None
_recon_scheduler: AsyncIOScheduler | None = None

# Track last sync status for health monitoring
_last_sync_status: dict[str, Any] = {
    "last_run_time": None,
    "last_success_time": None,
    "last_error": None,
    "total_runs": 0,
    "successful_runs": 0,
    "failed_runs": 0,
    "last_duration_seconds": None,
    "last_models_synced": 0,
}

# Track last price-refresh status (mirrors _last_sync_status).
_last_price_refresh_status: dict[str, Any] = {
    "last_run_time": None,
    "last_success_time": None,
    "last_error": None,
    "total_runs": 0,
    "successful_runs": 0,
    "failed_runs": 0,
    "last_duration_seconds": None,
    "last_prices_updated": 0,
}


async def warm_caches_after_sync(changed_providers: list[str]) -> None:
    """
    Proactively warm caches after a successful incremental sync.

    Runs as a fire-and-forget background task so no user pays the
    full cache rebuild cost on the first request after sync.
    Failures are non-fatal — logged as warnings only.
    """
    from src.db.models_catalog_db import get_models_stats
    from src.services.model_catalog_cache import (
        cache_catalog_stats,
        get_cached_full_catalog,
        warm_unique_models_cache_all_variants,
    )

    logger.info(
        f"Cache warming started after sync " f"(providers with changes: {changed_providers})"
    )

    # Brief delay to let DB writes propagate
    await asyncio.sleep(2)

    # Phase 1: Full catalog
    try:
        catalog = await asyncio.to_thread(get_cached_full_catalog)
        model_count = len(catalog) if catalog else 0
        logger.info(f"Cache warm [1/3]: Full catalog warmed ({model_count} models)")
    except Exception as e:
        logger.warning(f"Cache warm [1/3]: Full catalog warming failed (non-fatal): {e}")

    # Phase 2: Unique models (all filter/sort variants)
    try:
        warm_stats = await warm_unique_models_cache_all_variants()
        logger.info(
            f"Cache warm [2/3]: Unique models warmed "
            f"({warm_stats.get('successful', 0)}/{warm_stats.get('total_variants', 0)} variants)"
        )
    except Exception as e:
        logger.warning(f"Cache warm [2/3]: Unique models warming failed (non-fatal): {e}")

    # Phase 3: Catalog stats
    try:
        stats = await asyncio.to_thread(get_models_stats)
        if stats:
            cache_catalog_stats(stats)
            logger.info("Cache warm [3/3]: Catalog stats warmed")
        else:
            logger.warning("Cache warm [3/3]: get_models_stats returned empty")
    except Exception as e:
        logger.warning(f"Cache warm [3/3]: Catalog stats warming failed (non-fatal): {e}")

    logger.info("Cache warming complete after sync")


async def refresh_offers_projection_after(reason: str) -> None:
    """Best-effort refresh of model_provider_offers after a sync (Phase 1 pipeline).

    Keeps the smart router's offer set fresh as the catalog/prices change. Runs the
    blocking projection in a worker thread; never raises (a failure must not affect
    the sync that triggered it).
    """
    try:
        from src.services.model_offers_projection import refresh_offers_projection

        result = await asyncio.to_thread(refresh_offers_projection)
        logger.info("Offers projection refreshed after %s: %s", reason, result["summary"])
    except Exception as e:
        logger.warning("Offers projection refresh failed after %s (non-fatal): %s", reason, e)


async def run_scheduled_model_sync():
    """
    Run the scheduled model sync job.

    This function is called by APScheduler at the configured interval.
    It syncs all providers to the database via the canonical catalog-sync
    engine (``model_catalog_sync.sync_all_providers``): fetches the latest
    model catalog from every provider API and upserts to the DB.
    """
    from src.services.model_catalog_sync import sync_all_providers

    start_time = datetime.now(UTC)
    _last_sync_status["last_run_time"] = start_time
    _last_sync_status["total_runs"] += 1

    logger.info("=" * 80)
    logger.info("Starting scheduled model sync")
    logger.info("=" * 80)

    try:
        # Run the full sync in a background thread to avoid blocking event loop.
        result = await asyncio.to_thread(sync_all_providers, dry_run=False)

        # Calculate duration
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        if result.get("success"):
            # Success!
            _last_sync_status["successful_runs"] += 1
            _last_sync_status["last_success_time"] = end_time
            _last_sync_status["last_error"] = None
            _last_sync_status["last_duration_seconds"] = duration
            _last_sync_status["last_models_synced"] = result.get("total_models_synced", 0)

            logger.info("=" * 80)
            logger.info("✅ Scheduled sync SUCCESSFUL")
            logger.info(f"   Duration: {duration:.2f}s")
            logger.info(f"   Models fetched: {result.get('total_models_fetched', 0):,}")
            logger.info(f"   Models synced: {result.get('total_models_synced', 0):,}")
            logger.info(f"   Models skipped: {result.get('total_models_skipped', 0):,}")
            logger.info(f"   Providers processed: {result.get('providers_processed', 0)}")
            logger.info("=" * 80)

            # Proactively warm caches so no user pays the rebuild cost. A full
            # sync always refreshes the catalog, so warm on any successful run.
            if result.get("total_models_synced", 0) > 0:
                asyncio.create_task(
                    warm_caches_after_sync([]),
                    name="post_sync_cache_warm",
                )
                logger.info("Cache warming task queued after model sync")

            # Refresh the smart router's offer projection from the updated catalog.
            asyncio.create_task(
                refresh_offers_projection_after("model sync"),
                name="post_sync_offers_projection",
            )

        else:
            # Failed
            _last_sync_status["failed_runs"] += 1
            error_msg = result.get("error", "Unknown error")
            _last_sync_status["last_error"] = error_msg
            _last_sync_status["last_duration_seconds"] = duration

            logger.error("=" * 80)
            logger.error("❌ Scheduled model sync FAILED")
            logger.error(f"   Duration: {duration:.2f}s")
            logger.error(f"   Error: {error_msg}")
            logger.error("=" * 80)

    except Exception as e:
        # Unexpected error
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()

        _last_sync_status["failed_runs"] += 1
        _last_sync_status["last_error"] = str(e)
        _last_sync_status["last_duration_seconds"] = duration

        logger.exception("=" * 80)
        logger.exception("❌ Scheduled model sync EXCEPTION")
        logger.exception(f"   Duration: {duration:.2f}s")
        logger.exception(f"   Error: {e}")
        logger.exception("=" * 80)


def start_scheduler():
    """
    Start the APScheduler for scheduled model sync.

    Called during application startup (in app lifespan).
    Only starts if ENABLE_SCHEDULED_MODEL_SYNC is enabled.
    """
    global _scheduler

    # Check if scheduled sync is enabled
    if not Config.ENABLE_SCHEDULED_MODEL_SYNC:
        logger.info("Scheduled model sync DISABLED: ENABLE_SCHEDULED_MODEL_SYNC=false")
        return

    # Get sync interval
    interval_minutes = Config.MODEL_SYNC_INTERVAL_MINUTES

    logger.info("=" * 80)
    logger.info("🚀 Starting Scheduled Model Sync Service")
    logger.info("=" * 80)
    logger.info(f"   Interval: {interval_minutes} minutes")
    logger.info("=" * 80)

    try:
        # Create scheduler
        _scheduler = AsyncIOScheduler()

        # Add the sync job
        _scheduler.add_job(
            run_scheduled_model_sync,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="model_sync",
            name="Model Sync Job",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
            coalesce=True,  # Combine missed runs
        )

        # Start the scheduler
        _scheduler.start()

        logger.info("✅ Scheduled model sync service started successfully")
        logger.info(f"   Next sync in {interval_minutes} minutes")

    except Exception as e:
        logger.error(f"❌ Failed to start scheduled model sync service: {e}")
        logger.exception(e)


def stop_scheduler():
    """
    Stop the APScheduler gracefully.

    Called during application shutdown (in app lifespan).
    """
    global _scheduler

    if _scheduler is None:
        return

    logger.info("Stopping scheduled model sync service...")

    try:
        _scheduler.shutdown(wait=True)
        logger.info("✅ Scheduled model sync service stopped successfully")
    except Exception as e:
        logger.error(f"❌ Error stopping scheduled model sync service: {e}")
    finally:
        _scheduler = None


def get_sync_status() -> dict[str, Any]:
    """
    Get the current status of scheduled sync (for health monitoring).

    Returns:
        Dictionary with sync status metrics

    Example:
        >>> status = get_sync_status()
        >>> print(f"Last sync: {status['last_success_time']}")
        >>> print(f"Success rate: {status['success_rate']:.1f}%")
    """
    # Calculate success rate
    total_runs = _last_sync_status["total_runs"]
    successful_runs = _last_sync_status["successful_runs"]
    success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0

    # Calculate time since last sync
    last_success = _last_sync_status["last_success_time"]
    minutes_since_last_sync = None
    if last_success:
        delta = datetime.now(UTC) - last_success
        minutes_since_last_sync = delta.total_seconds() / 60

    # Determine health status
    is_healthy = True
    health_reason = "Healthy"

    if total_runs == 0:
        is_healthy = False
        health_reason = "No syncs run yet"
    elif (
        minutes_since_last_sync and minutes_since_last_sync > Config.MODEL_SYNC_INTERVAL_MINUTES * 2
    ):
        is_healthy = False
        health_reason = f"Last successful sync {minutes_since_last_sync:.0f} minutes ago (expected every {Config.MODEL_SYNC_INTERVAL_MINUTES} minutes)"
    elif success_rate < 50:
        is_healthy = False
        health_reason = f"Low success rate: {success_rate:.1f}%"

    return {
        # Status
        "is_healthy": is_healthy,
        "health_reason": health_reason,
        "enabled": Config.ENABLE_SCHEDULED_MODEL_SYNC,
        # Times
        "last_run_time": (
            _last_sync_status["last_run_time"].isoformat()
            if _last_sync_status["last_run_time"]
            else None
        ),
        "last_success_time": (
            _last_sync_status["last_success_time"].isoformat()
            if _last_sync_status["last_success_time"]
            else None
        ),
        "minutes_since_last_sync": (
            round(minutes_since_last_sync, 1) if minutes_since_last_sync else None
        ),
        # Counts
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "failed_runs": _last_sync_status["failed_runs"],
        "success_rate": round(success_rate, 1),
        # Last run details
        "last_error": _last_sync_status["last_error"],
        "last_duration_seconds": _last_sync_status["last_duration_seconds"],
        "last_models_synced": _last_sync_status["last_models_synced"],
        # Config
        "sync_interval_minutes": Config.MODEL_SYNC_INTERVAL_MINUTES,
    }


def trigger_manual_sync() -> dict[str, Any]:
    """
    Manually trigger a sync job (for admin endpoints).

    Returns:
        Status of the manual sync
    """
    logger.info("Manual sync triggered via API")

    # Run sync synchronously
    loop = asyncio.get_event_loop()
    loop.create_task(run_scheduled_model_sync())

    return {
        "success": True,
        "message": "Manual sync triggered - check logs for progress",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ============================================================================
# Lightweight price-only refresh scheduler
# ============================================================================


async def run_scheduled_price_refresh():
    """
    Run the lightweight, price-only refresh job.

    Called by APScheduler at PRICE_REFRESH_INTERVAL_MINUTES. Unlike the full
    sync, this only updates prices for models that already exist in the DB and
    never rebuilds/warms the catalog cache. The synchronous I/O runs in a worker
    thread so the event loop is never blocked.
    """
    from src.services.price_refresh import refresh_all_prices

    start_time = datetime.now(UTC)
    _last_price_refresh_status["last_run_time"] = start_time
    _last_price_refresh_status["total_runs"] += 1

    logger.info("Starting scheduled price-only refresh")

    try:
        result = await asyncio.to_thread(refresh_all_prices, dry_run=False)

        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()
        _last_price_refresh_status["last_duration_seconds"] = duration

        if result.get("success"):
            _last_price_refresh_status["successful_runs"] += 1
            _last_price_refresh_status["last_success_time"] = end_time
            _last_price_refresh_status["last_error"] = None
            _last_price_refresh_status["last_prices_updated"] = result.get("prices_updated", 0)
            logger.info(
                "Price refresh SUCCESSFUL in %.2fs | updated=%s unchanged=%s "
                "checked=%s failed=%s",
                duration,
                result.get("prices_updated", 0),
                result.get("prices_unchanged", 0),
                result.get("providers_checked", 0),
                result.get("providers_failed", 0),
            )
            # Prices feed the smart router's upstream_cost — re-project offers when
            # any price actually changed.
            if result.get("prices_updated", 0) > 0:
                asyncio.create_task(
                    refresh_offers_projection_after("price refresh"),
                    name="post_price_refresh_offers_projection",
                )
        else:
            # success=False means at least one provider failed; the rest still ran.
            _last_price_refresh_status["failed_runs"] += 1
            _last_price_refresh_status["last_error"] = str(result.get("errors"))
            _last_price_refresh_status["last_prices_updated"] = result.get("prices_updated", 0)
            logger.warning(
                "Price refresh completed with %s provider failure(s) in %.2fs | "
                "updated=%s errors=%s",
                result.get("providers_failed", 0),
                duration,
                result.get("prices_updated", 0),
                result.get("errors"),
            )

    except Exception as e:
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()
        _last_price_refresh_status["failed_runs"] += 1
        _last_price_refresh_status["last_error"] = str(e)
        _last_price_refresh_status["last_duration_seconds"] = duration
        logger.exception(f"Price refresh EXCEPTION after {duration:.2f}s: {e}")


def start_price_refresh_scheduler():
    """
    Start the APScheduler for the lightweight price-only refresh.

    Called during application startup (in app lifespan). Only starts if
    ENABLE_PRICE_REFRESH is enabled. Independent of ENABLE_SCHEDULED_MODEL_SYNC.
    """
    global _price_scheduler

    if not Config.ENABLE_PRICE_REFRESH:
        logger.info("Price refresh DISABLED: ENABLE_PRICE_REFRESH=false")
        return

    interval_minutes = Config.PRICE_REFRESH_INTERVAL_MINUTES

    logger.info(
        "Starting lightweight price-only refresh scheduler (interval: %s minutes)",
        interval_minutes,
    )

    try:
        _price_scheduler = AsyncIOScheduler()
        _price_scheduler.add_job(
            run_scheduled_price_refresh,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="price_refresh",
            name="Price Refresh Job",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
            coalesce=True,  # Combine missed runs
        )
        _price_scheduler.start()
        logger.info("✅ Price refresh scheduler started (next run in %s minutes)", interval_minutes)
    except Exception as e:
        logger.error(f"❌ Failed to start price refresh scheduler: {e}")
        logger.exception(e)


def stop_price_refresh_scheduler():
    """Stop the price-refresh APScheduler gracefully (called during shutdown)."""
    global _price_scheduler

    if _price_scheduler is None:
        return

    logger.info("Stopping price refresh scheduler...")

    try:
        _price_scheduler.shutdown(wait=True)
        logger.info("✅ Price refresh scheduler stopped successfully")
    except Exception as e:
        logger.error(f"❌ Error stopping price refresh scheduler: {e}")
    finally:
        _price_scheduler = None


def get_price_refresh_status() -> dict[str, Any]:
    """Get the current status of the price-refresh job (for health monitoring)."""
    total_runs = _last_price_refresh_status["total_runs"]
    successful_runs = _last_price_refresh_status["successful_runs"]
    success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0

    return {
        "enabled": Config.ENABLE_PRICE_REFRESH,
        "interval_minutes": Config.PRICE_REFRESH_INTERVAL_MINUTES,
        "last_run_time": (
            _last_price_refresh_status["last_run_time"].isoformat()
            if _last_price_refresh_status["last_run_time"]
            else None
        ),
        "last_success_time": (
            _last_price_refresh_status["last_success_time"].isoformat()
            if _last_price_refresh_status["last_success_time"]
            else None
        ),
        "total_runs": total_runs,
        "successful_runs": successful_runs,
        "failed_runs": _last_price_refresh_status["failed_runs"],
        "success_rate": round(success_rate, 1),
        "last_error": _last_price_refresh_status["last_error"],
        "last_duration_seconds": _last_price_refresh_status["last_duration_seconds"],
        "last_prices_updated": _last_price_refresh_status["last_prices_updated"],
    }


# ============================================================================
# Credit-ledger reconciliation (Gatewayz One Phase 3, item 4) — scheduled,
# read-only. Compares the shadow ledger against live billing over a recent
# window and logs the result; drift logs at ERROR so it is alertable. It never
# mutates billing.
# ============================================================================

_last_recon_status: dict[str, Any] = {
    "last_run_time": None,
    "last_ok": None,
    "last_total_drift": None,
    "last_ledger_refs": None,
}


async def run_scheduled_ledger_reconciliation():
    """Reconcile the shadow credit ledger vs live billing over the recent window."""
    from datetime import timedelta

    from src.services.billing.ledger_reconciliation import reconcile_window

    now = datetime.now(UTC)
    since = (now - timedelta(hours=Config.LEDGER_RECONCILIATION_WINDOW_HOURS)).isoformat()
    until = now.isoformat()
    _last_recon_status["last_run_time"] = now

    try:
        report, admin_count = await asyncio.to_thread(reconcile_window, since, until)
        _last_recon_status["last_ok"] = report.ok
        _last_recon_status["last_total_drift"] = str(report.total_drift)
        _last_recon_status["last_ledger_refs"] = report.ledger_ref_count

        if report.ledger_ref_count == 0:
            logger.info(
                "Ledger reconciliation: no ledger rows in last %sh (shadow not yet accruing)",
                Config.LEDGER_RECONCILIATION_WINDOW_HOURS,
            )
        elif report.ok:
            logger.info(
                "✅ Ledger reconciliation OK | refs=%s revenue=%s usage=%s drift=%s (±%s)",
                report.ledger_ref_count,
                report.total_ledger_revenue,
                report.total_usage_cost,
                report.total_drift,
                report.tolerance,
            )
        else:
            offenders = [u for u in report.per_user if not u.within_tolerance]
            logger.error(
                "❌ Ledger reconciliation DRIFT | refs=%s drift=%s unbalanced=%s "
                "users_over_tolerance=%s (admins excluded=%s)",
                report.ledger_ref_count,
                report.total_drift,
                len(report.unbalanced_refs),
                len(offenders),
                admin_count,
            )
    except Exception as e:
        logger.warning("Ledger reconciliation failed (non-fatal): %s", e)


def start_ledger_reconciliation_scheduler():
    """Start the APScheduler for credit-ledger reconciliation (app lifespan)."""
    global _recon_scheduler

    if not Config.ENABLE_LEDGER_RECONCILIATION:
        logger.info("Ledger reconciliation DISABLED: ENABLE_LEDGER_RECONCILIATION=false")
        return

    interval_minutes = Config.LEDGER_RECONCILIATION_INTERVAL_MINUTES
    logger.info(
        "Starting credit-ledger reconciliation scheduler (interval: %s min)", interval_minutes
    )
    try:
        _recon_scheduler = AsyncIOScheduler()
        _recon_scheduler.add_job(
            run_scheduled_ledger_reconciliation,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="ledger_reconciliation",
            name="Credit Ledger Reconciliation Job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _recon_scheduler.start()
        logger.info(
            "✅ Ledger reconciliation scheduler started (next run in %s min)", interval_minutes
        )
    except Exception as e:
        logger.error("❌ Failed to start ledger reconciliation scheduler: %s", e)
        logger.exception(e)


def stop_ledger_reconciliation_scheduler():
    """Stop the reconciliation APScheduler gracefully (called during shutdown)."""
    global _recon_scheduler

    if _recon_scheduler is None:
        return
    logger.info("Stopping ledger reconciliation scheduler...")
    try:
        _recon_scheduler.shutdown(wait=True)
        logger.info("✅ Ledger reconciliation scheduler stopped successfully")
    except Exception as e:
        logger.error("❌ Error stopping ledger reconciliation scheduler: %s", e)
    finally:
        _recon_scheduler = None


# ============================================================================
# Nightly pricing-drift monitor — scheduled, read-only. We bill inference at
# catalog_price * Config.PRICING_MARKUP; if a provider raises its price and our
# catalog goes stale, we could bill below provider cost even with markup applied.
# This job audits active-provider catalog pricing against current OpenRouter
# reference pricing and alerts (log at ERROR + Sentry) on drift. It never mutates
# prices or models — alert only, exactly like ledger reconciliation above.
# ============================================================================

_pricing_drift_scheduler: AsyncIOScheduler | None = None

_last_pricing_drift_status: dict[str, Any] = {
    "last_run_time": None,
    "last_ok": None,
    "last_checked": None,
    "last_drift_count": None,
    "last_unpriced_count": None,
    "last_worst_deficit_pct": None,
    "last_error": None,
}


async def run_scheduled_pricing_drift_audit():
    """Run the nightly pricing-drift audit and alert on any margin-leak risk.

    Read-only: never mutates prices/models. On drift or unpriced active models,
    logs at ERROR (alertable via log-based alerting) and captures a Sentry
    message so it pages independently of log scraping.
    """
    from src.services.billing.pricing_drift_monitor import audit_pricing_drift

    now = datetime.now(UTC)
    _last_pricing_drift_status["last_run_time"] = now

    try:
        result = await asyncio.to_thread(audit_pricing_drift)

        _last_pricing_drift_status["last_ok"] = result.get("ok")
        _last_pricing_drift_status["last_checked"] = result.get("checked")
        _last_pricing_drift_status["last_drift_count"] = len(result.get("drift", []))
        _last_pricing_drift_status["last_unpriced_count"] = len(result.get("unpriced", []))
        _last_pricing_drift_status["last_worst_deficit_pct"] = result.get("worst_deficit_pct")
        _last_pricing_drift_status["last_error"] = None

        if result.get("ok"):
            logger.info(
                "✅ Pricing drift audit OK | checked=%s no drift, no unpriced models",
                result.get("checked", 0),
            )
            return

        drift = result.get("drift", [])
        unpriced = result.get("unpriced", [])
        logger.error(
            "❌ Pricing drift audit found billing risk | checked=%s drift=%s "
            "unpriced=%s worst_deficit_pct=%.2f%% | worst_examples=%s",
            result.get("checked", 0),
            len(drift),
            len(unpriced),
            result.get("worst_deficit_pct", 0.0),
            drift[:5],
        )

        try:
            import sentry_sdk

            sentry_sdk.capture_message(
                "Pricing drift detected: catalog price * markup below provider cost "
                f"for {len(drift)} model(s), {len(unpriced)} unpriced active model(s) "
                f"(worst deficit {result.get('worst_deficit_pct', 0.0):.2f}%)",
                level="error",
            )
        except Exception as sentry_error:
            logger.warning("Failed to capture pricing drift to Sentry: %s", sentry_error)

    except Exception as e:
        _last_pricing_drift_status["last_error"] = str(e)
        logger.exception("Pricing drift audit EXCEPTION: %s", e)


def start_pricing_drift_scheduler():
    """Start the APScheduler for the nightly pricing-drift monitor (app lifespan)."""
    global _pricing_drift_scheduler

    if not Config.ENABLE_PRICING_DRIFT_MONITOR:
        logger.info("Pricing drift monitor DISABLED: ENABLE_PRICING_DRIFT_MONITOR=false")
        return

    interval_minutes = Config.PRICING_DRIFT_INTERVAL_MINUTES
    logger.info(
        "Starting pricing drift monitor scheduler (interval: %s min)", interval_minutes
    )
    try:
        _pricing_drift_scheduler = AsyncIOScheduler()
        _pricing_drift_scheduler.add_job(
            run_scheduled_pricing_drift_audit,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="pricing_drift_monitor",
            name="Pricing Drift Monitor Job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _pricing_drift_scheduler.start()
        logger.info(
            "✅ Pricing drift monitor scheduler started (next run in %s min)", interval_minutes
        )
    except Exception as e:
        logger.error("❌ Failed to start pricing drift monitor scheduler: %s", e)
        logger.exception(e)


def stop_pricing_drift_scheduler():
    """Stop the pricing-drift-monitor APScheduler gracefully (called during shutdown)."""
    global _pricing_drift_scheduler

    if _pricing_drift_scheduler is None:
        return
    logger.info("Stopping pricing drift monitor scheduler...")
    try:
        _pricing_drift_scheduler.shutdown(wait=True)
        logger.info("✅ Pricing drift monitor scheduler stopped successfully")
    except Exception as e:
        logger.error("❌ Error stopping pricing drift monitor scheduler: %s", e)
    finally:
        _pricing_drift_scheduler = None


def get_pricing_drift_status() -> dict[str, Any]:
    """Get the current status of the pricing-drift monitor (for health monitoring)."""
    return {
        "enabled": Config.ENABLE_PRICING_DRIFT_MONITOR,
        "interval_minutes": Config.PRICING_DRIFT_INTERVAL_MINUTES,
        "last_run_time": (
            _last_pricing_drift_status["last_run_time"].isoformat()
            if _last_pricing_drift_status["last_run_time"]
            else None
        ),
        "last_ok": _last_pricing_drift_status["last_ok"],
        "last_checked": _last_pricing_drift_status["last_checked"],
        "last_drift_count": _last_pricing_drift_status["last_drift_count"],
        "last_unpriced_count": _last_pricing_drift_status["last_unpriced_count"],
        "last_worst_deficit_pct": _last_pricing_drift_status["last_worst_deficit_pct"],
        "last_error": _last_pricing_drift_status["last_error"],
    }
