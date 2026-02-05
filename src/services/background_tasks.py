"""
Background task management for non-blocking operations
Handles activity logging and other I/O operations in the background
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.db.activity import log_activity as db_log_activity

logger = logging.getLogger(__name__)

# Dedicated thread pool for heavy DB operations (catalog refresh, etc.)
# This prevents heavy background DB work from starving the default executor
# which is used by asyncio.to_thread() throughout the app.
_db_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="db-background")

# Queue for background tasks
_background_tasks = []
_task_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None


async def log_activity_async(
    user_id: int,
    model: str,
    provider: str,
    tokens: int,
    cost: float,
    speed: float = 0.0,
    finish_reason: str = "stop",
    app: str = "API",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Log activity asynchronously in the background
    Non-blocking activity logging to improve session creation performance

    Args:
        user_id: User ID
        model: Model name
        provider: Provider name
        tokens: Tokens used
        cost: Cost in dollars
        speed: Tokens per second
        finish_reason: Completion reason
        app: Application name
        metadata: Additional metadata
    """
    try:
        # Run database operation in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            db_log_activity,
            user_id,
            model,
            provider,
            tokens,
            cost,
            speed,
            finish_reason,
            app,
            metadata,
        )
    except Exception as e:
        logger.error(
            f"Failed to log activity in background for user {user_id}: {e}",
            exc_info=True,
        )
        # Don't raise - background tasks should not affect main flow


def log_activity_background(
    user_id: int,
    model: str,
    provider: str,
    tokens: int,
    cost: float,
    speed: float = 0.0,
    finish_reason: str = "stop",
    app: str = "API",
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Queue activity logging as a background task (non-awaitable)

    This is useful when you can't use async/await and need to fire-and-forget
    activity logging. Use this in synchronous contexts.

    Args:
        user_id: User ID
        model: Model name
        provider: Provider name
        tokens: Tokens used
        cost: Cost in dollars
        speed: Tokens per second
        finish_reason: Completion reason
        app: Application name
        metadata: Additional metadata
    """
    try:
        # Try to create a task if in async context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the coroutine as a task
                task = loop.create_task(
                    log_activity_async(
                        user_id=user_id,
                        model=model,
                        provider=provider,
                        tokens=tokens,
                        cost=cost,
                        speed=speed,
                        finish_reason=finish_reason,
                        app=app,
                        metadata=metadata,
                    )
                )
                logger.debug(f"Queued background activity logging task for user {user_id}")
                return
        except RuntimeError:
            # No event loop running, fall through to sync call
            pass

        # Fall back to synchronous logging (will block slightly)
        db_log_activity(
            user_id=user_id,
            model=model,
            provider=provider,
            tokens=tokens,
            cost=cost,
            speed=speed,
            finish_reason=finish_reason,
            app=app,
            metadata=metadata,
        )

    except Exception as e:
        logger.error(
            f"Failed to queue background activity logging for user {user_id}: {e}",
            exc_info=True,
        )


def get_pending_tasks_count() -> int:
    """Get count of pending background tasks (for monitoring)"""
    try:
        loop = asyncio.get_event_loop()
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        return len(tasks)
    except Exception:
        return 0


# === Router Health Snapshot Background Task ===

_health_snapshot_task: asyncio.Task | None = None
_health_snapshot_stop_event: asyncio.Event | None = None


async def update_router_health_snapshots() -> None:
    """
    Background task to update router health snapshots every 30 seconds.

    This task:
    1. Collects health data from the intelligent health monitor
    2. Writes pre-computed healthy model lists to Redis
    3. Router then reads ONE key per request instead of N awaits

    This is critical for meeting the < 2ms router latency budget.
    """
    from src.services.health_snapshots import get_health_snapshot_service

    logger.info("Starting router health snapshot background task")
    service = get_health_snapshot_service()

    while True:
        try:
            # Check if we should stop
            if _health_snapshot_stop_event and _health_snapshot_stop_event.is_set():
                logger.info("Router health snapshot task stopping")
                break

            # Collect health data from existing health monitoring
            health_data = await _collect_model_health_data()

            # Update snapshots
            await service.update_health_snapshot(health_data)

            logger.debug(f"Updated router health snapshots with {len(health_data)} models")

        except Exception as e:
            logger.error(f"Error updating router health snapshots: {e}", exc_info=True)

        # Wait 30 seconds before next update
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Router health snapshot task cancelled")
            break


async def _collect_model_health_data() -> dict[str, dict[str, Any]]:
    """
    Collect health data for all models from existing health monitoring.

    Returns dict mapping model_id -> health info
    """
    from datetime import datetime, timezone

    health_data = {}

    try:
        # Try to get health data from the intelligent health monitor
        from src.services.intelligent_health_monitor import get_all_provider_health

        provider_health = await get_all_provider_health()

        if provider_health:
            now = datetime.now(timezone.utc)
            for provider, health in provider_health.items():
                # Create synthetic model IDs based on provider
                # In production, this should be enhanced to track per-model health
                model_id = f"{provider}/default"
                health_data[model_id] = {
                    "health_score": health.get("score", 100),
                    "consecutive_failures": health.get("consecutive_failures", 0),
                    "last_failure_at": health.get("last_failure_at"),
                    "last_updated": now.isoformat(),
                }

    except ImportError:
        logger.debug("Intelligent health monitor not available, using defaults")
    except Exception as e:
        logger.warning(f"Could not collect health data from monitor: {e}")

    # If no health data, assume all models are healthy
    if not health_data:
        from datetime import datetime, timezone
        from src.services.health_snapshots import SMALL_TIER_POOL, MEDIUM_TIER_POOL

        now = datetime.now(timezone.utc)
        all_models = set(SMALL_TIER_POOL) | set(MEDIUM_TIER_POOL)

        for model_id in all_models:
            health_data[model_id] = {
                "health_score": 100,
                "consecutive_failures": 0,
                "last_failure_at": None,
                "last_updated": now.isoformat(),
            }

    return health_data


async def get_all_provider_health() -> dict[str, dict[str, Any]]:
    """
    Fallback function if intelligent_health_monitor is not available.
    Returns empty dict (all models treated as healthy).
    """
    return {}


def start_router_health_snapshot_task() -> None:
    """
    Start the router health snapshot background task.
    Call this during application startup.
    """
    global _health_snapshot_task, _health_snapshot_stop_event

    try:
        loop = asyncio.get_running_loop()
        _health_snapshot_stop_event = asyncio.Event()
        _health_snapshot_task = loop.create_task(update_router_health_snapshots())
        logger.info("Router health snapshot background task started")
    except RuntimeError:
        # No running event loop
        logger.warning("Event loop not running, cannot start health snapshot task")
    except Exception as e:
        logger.error(f"Failed to start router health snapshot task: {e}")


def stop_router_health_snapshot_task() -> None:
    """
    Stop the router health snapshot background task.
    Call this during application shutdown.
    """
    global _health_snapshot_task, _health_snapshot_stop_event

    if _health_snapshot_stop_event:
        _health_snapshot_stop_event.set()

    if _health_snapshot_task:
        _health_snapshot_task.cancel()
        logger.info("Router health snapshot background task stopped")


# === Model Catalog Background Refresh (Prevent 499 Deadlocks) ===

_catalog_refresh_task: asyncio.Task | None = None
_catalog_refresh_stop_event: asyncio.Event | None = None


async def update_full_model_catalog_loop() -> None:
    """
    Background task to refresh the full model catalog every 14 minutes.
    
    Why this is needed:
    - Prevents cache TTL (15m) from expiring during user requests.
    - Eliminates the "thundering herd" of DB queries when cache is cold.
    - Acts as a DB connection keep-alive during idle periods.
    - Resource usage is negligible (one efficient query every 14m).
    """
    from src.db.models_catalog_db import get_all_models_for_catalog, transform_db_models_batch
    from src.services.model_catalog_cache import cache_full_catalog
    
    logger.info("Starting model catalog background refresh loop (interval: 14m)")
    
    REFRESH_INTERVAL_SECONDS = 14 * 60  # 14 minutes
    
    # Startup delay: let preload_hot_models_cache (5s delay) handle the first
    # cache warm. This task takes over for periodic refreshes after that.
    STARTUP_DELAY = 120  # 2 minutes - well after initial preload completes
    logger.info(f"Waiting {STARTUP_DELAY}s before first model catalog refresh...")
    try:
        await asyncio.sleep(STARTUP_DELAY)
    except asyncio.CancelledError:
        logger.info("Model catalog refresh task cancelled during startup delay")
        return
    
    while True:
        try:
            # Check if we should stop
            if _catalog_refresh_stop_event and _catalog_refresh_stop_event.is_set():
                logger.info("Model catalog refresh task stopping")
                break
                
            logger.info("🔄 Background Refresh: Updating full model catalog...")
            
            # 1. Fetch from DB (optimized query)
            # Use dedicated DB executor to avoid starving the default thread pool
            loop = asyncio.get_running_loop()
            db_models = await loop.run_in_executor(_db_executor, get_all_models_for_catalog, False)
            
            # 2. Transform to API format
            api_models = transform_db_models_batch(db_models)
            
            # 3. Update Cache
            # We set TTL to 15m, but we refresh every 14m to ensure overlap
            cache_full_catalog(api_models, ttl=900)
            
            logger.info(f"✅ Background Refresh: Updated catalog with {len(api_models)} models")
            
        except Exception as e:
            logger.error(f"Error in model catalog refresh loop: {e}", exc_info=True)
            
        # Wait for next interval
        try:
            # Use wait_for to allow immediate cancellation
            if _catalog_refresh_stop_event:
                # Create a future that waits for the stop event
                wait_task = asyncio.create_task(_catalog_refresh_stop_event.wait())
                try:
                    await asyncio.wait_for(wait_task, timeout=REFRESH_INTERVAL_SECONDS)
                    # If we get here, the stop event was set
                    break
                except asyncio.TimeoutError:
                    # Timeout reached, run loop again
                    pass
            else:
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Model catalog refresh task cancelled")
            break


def start_model_catalog_refresh_task() -> None:
    """Start the model catalog refresh background task."""
    global _catalog_refresh_task, _catalog_refresh_stop_event
    
    try:
        if _catalog_refresh_task and not _catalog_refresh_task.done():
            logger.warning("Model catalog refresh task already running")
            return

        loop = asyncio.get_running_loop()
        _catalog_refresh_stop_event = asyncio.Event()
        _catalog_refresh_task = loop.create_task(update_full_model_catalog_loop())
        logger.info("Model catalog refresh background task started")
    except RuntimeError:
        logger.warning("Event loop not running, cannot start catalog refresh task")
    except Exception as e:
        logger.error(f"Failed to start catalog refresh task: {e}")


def stop_model_catalog_refresh_task() -> None:
    """Stop the model catalog refresh background task."""
    global _catalog_refresh_task, _catalog_refresh_stop_event
    
    if _catalog_refresh_stop_event:
        _catalog_refresh_stop_event.set()
        
    if _catalog_refresh_task:
        _catalog_refresh_task.cancel()
        logger.info("Model catalog refresh background task stopped")

