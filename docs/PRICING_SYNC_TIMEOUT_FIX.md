# Pricing Sync Timeout Fix - Implementation Guide

**Issue:** #958 - Critical timeout issues with pricing sync operations

## Problem Summary

1. **504 Gateway Timeout**: Sync endpoint times out after 55 seconds (Railway limit)
2. **Stuck Syncs**: 15+ sync records stuck in `in_progress` status
3. **No Cleanup**: No automatic cleanup for failed/stuck syncs

## Solution Architecture

### Approach: Async Background Tasks with Status Polling

```
Before (Synchronous):
API Request → Run Sync (60s+) → Response [TIMEOUT!]

After (Asynchronous):
API Request → Queue Sync → Return Job ID (instant)
              ↓
         Background Task → Update Status

Client: Poll Status Endpoint → Get Progress → Complete!
```

---

## Implementation Steps

### Step 1: Update Admin Endpoint (Make it Async)

**File:** `src/routes/admin.py`

**Change:** Replace synchronous sync with async job queueing

```python
# BEFORE (lines 2580-2647):
@router.post("/admin/pricing/scheduler/trigger", tags=["admin", "pricing"])
async def trigger_manual_pricing_sync(admin_user: dict = Depends(require_admin)):
    """Manually trigger a pricing sync..."""
    try:
        from src.services.pricing_sync_scheduler import trigger_manual_sync

        logger.info(f"Manual pricing sync triggered by admin: {admin_user.get('email')}")

        # THIS IS THE PROBLEM - Runs synchronously and times out
        result = await trigger_manual_sync()  # ❌ BLOCKS for 60+ seconds

        return {
            "success": result["status"] == "success",
            **result,
            "triggered_by": admin_user.get("email"),
        }
    except Exception as e:
        logger.error(f"Failed to trigger manual sync: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger manual sync: {str(e)}")
```

**Replace with:**

```python
# AFTER (Fixed version):
@router.post("/admin/pricing/scheduler/trigger", tags=["admin", "pricing"])
async def trigger_manual_pricing_sync(
    background_tasks: BackgroundTasks,
    admin_user: dict = Depends(require_admin)
):
    """
    Manually trigger a pricing sync outside the regular schedule.

    **NEW**: This endpoint now returns immediately with a job ID.
    Poll GET /admin/pricing/sync/{sync_id} to check status.

    **Authentication**: Requires admin role

    **Example Response**:
    ```json
    {
        "success": true,
        "sync_id": "abc123",
        "status": "queued",
        "message": "Pricing sync queued for background execution",
        "triggered_by": "admin@example.com",
        "poll_url": "/admin/pricing/sync/abc123"
    }
    ```
    """
    try:
        from src.services.pricing_sync_scheduler import queue_background_sync

        logger.info(f"Manual pricing sync triggered by admin: {admin_user.get('email')}")

        # Queue sync in background (returns immediately)
        sync_id = await queue_background_sync(
            triggered_by=f"admin:{admin_user.get('email')}",
            background_tasks=background_tasks
        )

        return {
            "success": True,
            "sync_id": sync_id,
            "status": "queued",
            "message": "Pricing sync queued for background execution",
            "triggered_by": admin_user.get("email"),
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "poll_url": f"/admin/pricing/sync/{sync_id}",
            "estimated_duration_seconds": "30-120"
        }

    except Exception as e:
        logger.error(f"Failed to queue manual sync: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue manual sync: {str(e)}"
        )
```

---

### Step 2: Add New Status Polling Endpoint

**File:** `src/routes/admin.py`

**Add after the trigger endpoint:**

```python
@router.get("/admin/pricing/sync/{sync_id}", tags=["admin", "pricing"])
async def get_pricing_sync_status(
    sync_id: str,
    admin_user: dict = Depends(require_admin)
):
    """
    Get the status of a background pricing sync job.

    Use this endpoint to poll for sync progress after triggering
    a sync via POST /admin/pricing/scheduler/trigger

    **Authentication**: Requires admin role

    **Status Values**:
    - `queued`: Sync is waiting to start
    - `in_progress`: Sync is currently running
    - `completed`: Sync finished successfully
    - `failed`: Sync encountered an error

    **Example Response**:
    ```json
    {
        "sync_id": "abc123",
        "status": "completed",
        "started_at": "2026-01-26T12:00:00Z",
        "completed_at": "2026-01-26T12:02:30Z",
        "duration_seconds": 150.5,
        "providers_synced": ["openrouter", "featherless"],
        "total_models_updated": 245,
        "total_models_skipped": 12,
        "total_errors": 0,
        "results": {...}
    }
    ```
    """
    try:
        from src.services.pricing_sync_scheduler import get_sync_job_status

        # Get sync status from database
        status = await get_sync_job_status(sync_id)

        if not status:
            raise HTTPException(
                status_code=404,
                detail=f"Sync job {sync_id} not found"
            )

        return status

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving sync status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve sync status: {str(e)}"
        )


@router.get("/admin/pricing/syncs/active", tags=["admin", "pricing"])
async def get_active_pricing_syncs(admin_user: dict = Depends(require_admin)):
    """
    Get all currently active (queued or in_progress) pricing syncs.

    **Authentication**: Requires admin role

    **Example Response**:
    ```json
    {
        "active_syncs": [
            {
                "sync_id": "abc123",
                "status": "in_progress",
                "started_at": "2026-01-26T12:00:00Z",
                "progress_percent": 45
            }
        ],
        "count": 1
    }
    ```
    """
    try:
        from src.services.pricing_sync_scheduler import get_active_syncs

        active_syncs = await get_active_syncs()

        return {
            "active_syncs": active_syncs,
            "count": len(active_syncs)
        }

    except Exception as e:
        logger.error(f"Error retrieving active syncs: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve active syncs: {str(e)}"
        )
```

---

### Step 3: Update Pricing Sync Scheduler Service

**File:** `src/services/pricing_sync_scheduler.py`

**Add these new functions at the end of the file:**

```python
# Add to imports at top:
from uuid import uuid4
from fastapi import BackgroundTasks

# Add these new functions:

async def queue_background_sync(
    triggered_by: str = "manual",
    background_tasks: BackgroundTasks = None
) -> str:
    """
    Queue a pricing sync to run in the background.

    This function returns immediately with a sync job ID,
    allowing the HTTP request to complete without waiting
    for the sync to finish.

    Args:
        triggered_by: Who/what triggered the sync
        background_tasks: FastAPI BackgroundTasks instance

    Returns:
        Sync job ID for status polling
    """
    from src.config.supabase_config import get_supabase_client

    sync_id = str(uuid4())
    supabase = get_supabase_client()

    logger.info(f"Queueing background sync {sync_id} (triggered_by={triggered_by})")

    try:
        # Create sync log record immediately with status 'queued'
        supabase.table('pricing_sync_log').insert({
            'id': sync_id,
            'provider_slug': 'all',
            'status': 'queued',
            'sync_started_at': datetime.now(timezone.utc).isoformat(),
            'triggered_by': triggered_by,
            'models_fetched': 0,
            'models_updated': 0,
            'models_skipped': 0,
            'errors': 0
        }).execute()

        # Queue the actual sync work
        if background_tasks:
            background_tasks.add_task(_run_background_sync_with_error_handling, sync_id, triggered_by)
        else:
            # Fallback: use asyncio if no BackgroundTasks available
            import asyncio
            asyncio.create_task(
                _run_background_sync_with_error_handling(sync_id, triggered_by),
                name=f"pricing_sync_{sync_id}"
            )

        logger.info(f"✅ Background sync {sync_id} queued successfully")
        return sync_id

    except Exception as e:
        logger.error(f"Failed to queue background sync: {e}", exc_info=True)
        raise


async def _run_background_sync_with_error_handling(sync_id: str, triggered_by: str) -> None:
    """
    Run the pricing sync in background with comprehensive error handling.

    This ensures the sync status is ALWAYS updated, even if errors occur.
    This prevents stuck syncs.
    """
    from src.config.supabase_config import get_supabase_client
    from src.services.pricing_sync_service import run_scheduled_sync

    supabase = get_supabase_client()
    start_time = time.time()

    try:
        # Update status to in_progress
        supabase.table('pricing_sync_log').update({
            'status': 'in_progress',
            'sync_started_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', sync_id).execute()

        logger.info(f"🔄 Background sync {sync_id} started")

        # Run the actual sync
        result = await run_scheduled_sync(triggered_by=triggered_by)

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Update status to success
        supabase.table('pricing_sync_log').update({
            'status': 'success',
            'sync_completed_at': datetime.now(timezone.utc).isoformat(),
            'duration_ms': duration_ms,
            'models_fetched': result.get('total_models_fetched', 0),
            'models_updated': result.get('total_models_updated', 0),
            'models_skipped': result.get('total_models_skipped', 0),
            'errors': result.get('total_errors', 0),
            'error_message': None
        }).eq('id', sync_id).execute()

        logger.info(
            f"✅ Background sync {sync_id} completed successfully "
            f"(duration: {duration_ms}ms, updated: {result.get('total_models_updated', 0)} models)"
        )

    except Exception as e:
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        logger.error(f"❌ Background sync {sync_id} failed: {e}", exc_info=True)

        # CRITICAL: Always update status even on error
        try:
            supabase.table('pricing_sync_log').update({
                'status': 'failed',
                'sync_completed_at': datetime.now(timezone.utc).isoformat(),
                'duration_ms': duration_ms,
                'error_message': str(e)[:500]  # Limit error message length
            }).eq('id', sync_id).execute()
        except Exception as update_error:
            logger.error(
                f"CRITICAL: Failed to update sync status for {sync_id}: {update_error}",
                exc_info=True
            )


async def get_sync_job_status(sync_id: str) -> dict | None:
    """
    Get the status of a pricing sync job.

    Args:
        sync_id: The sync job ID

    Returns:
        Sync status dict or None if not found
    """
    from src.config.supabase_config import get_supabase_client

    supabase = get_supabase_client()

    try:
        response = supabase.table('pricing_sync_log').select('*').eq('id', sync_id).execute()

        if not response.data:
            return None

        sync = response.data[0]

        # Calculate progress percentage
        progress = 0
        if sync['status'] == 'queued':
            progress = 0
        elif sync['status'] == 'in_progress':
            progress = 50  # Rough estimate
        elif sync['status'] in ('success', 'completed'):
            progress = 100
        elif sync['status'] == 'failed':
            progress = 0

        return {
            'sync_id': sync_id,
            'status': sync['status'],
            'started_at': sync['sync_started_at'],
            'completed_at': sync.get('sync_completed_at'),
            'duration_ms': sync.get('duration_ms'),
            'duration_seconds': sync.get('duration_ms') / 1000 if sync.get('duration_ms') else None,
            'provider_slug': sync['provider_slug'],
            'triggered_by': sync['triggered_by'],
            'models_fetched': sync.get('models_fetched', 0),
            'models_updated': sync.get('models_updated', 0),
            'models_skipped': sync.get('models_skipped', 0),
            'errors': sync.get('errors', 0),
            'error_message': sync.get('error_message'),
            'progress_percent': progress
        }

    except Exception as e:
        logger.error(f"Error getting sync status for {sync_id}: {e}")
        return None


async def get_active_syncs() -> list:
    """
    Get all active (queued or in_progress) pricing syncs.

    Returns:
        List of active sync status dicts
    """
    from src.config.supabase_config import get_supabase_client

    supabase = get_supabase_client()

    try:
        response = supabase.table('pricing_sync_log').select('*').in_('status', ['queued', 'in_progress']).order('sync_started_at', desc=True).execute()

        return [
            {
                'sync_id': sync['id'],
                'status': sync['status'],
                'started_at': sync['sync_started_at'],
                'provider_slug': sync['provider_slug'],
                'triggered_by': sync['triggered_by'],
                'progress_percent': 0 if sync['status'] == 'queued' else 50
            }
            for sync in response.data
        ]

    except Exception as e:
        logger.error(f"Error getting active syncs: {e}")
        return []
```

---

### Step 4: Add Cleanup Job for Stuck Syncs

**File:** `src/services/pricing_sync_cleanup.py` (NEW FILE)

```python
"""
Pricing Sync Cleanup Service

Automatically cleans up stuck pricing sync records that failed to update their status.
This prevents database pollution from syncs that crashed or timed out.

This should run as a scheduled job (e.g., every 15 minutes).
"""

import logging
from datetime import datetime, timedelta, timezone

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


async def cleanup_stuck_syncs(timeout_minutes: int = 10) -> dict:
    """
    Find and mark stuck syncs as failed.

    A sync is considered "stuck" if:
    - Status is 'in_progress' or 'queued'
    - Started more than timeout_minutes ago
    - No completion timestamp

    Args:
        timeout_minutes: How many minutes before considering a sync stuck

    Returns:
        Dict with cleanup stats
    """
    supabase = get_supabase_client()

    try:
        # Calculate cutoff time
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        cutoff_str = cutoff_time.isoformat()

        logger.info(f"Looking for stuck syncs (started before {cutoff_str})")

        # Find stuck syncs
        response = supabase.table('pricing_sync_log').select('id, provider_slug, sync_started_at, triggered_by').in_('status', ['in_progress', 'queued']).lt('sync_started_at', cutoff_str).is_('sync_completed_at', 'null').execute()

        stuck_syncs = response.data

        if not stuck_syncs:
            logger.info("✅ No stuck syncs found")
            return {'stuck_syncs_found': 0, 'syncs_cleaned': 0}

        logger.warning(f"Found {len(stuck_syncs)} stuck syncs")

        # Mark each as failed
        cleaned_count = 0
        for sync in stuck_syncs:
            try:
                logger.warning(
                    f"Cleaning stuck sync: id={sync['id']}, "
                    f"provider={sync['provider_slug']}, "
                    f"started={sync['sync_started_at']}"
                )

                supabase.table('pricing_sync_log').update({
                    'status': 'failed',
                    'sync_completed_at': datetime.now(timezone.utc).isoformat(),
                    'error_message': f'Sync timeout - auto-cleaned after {timeout_minutes} minutes'
                }).eq('id', sync['id']).execute()

                cleaned_count += 1

            except Exception as e:
                logger.error(f"Failed to clean stuck sync {sync['id']}: {e}")

        logger.info(f"✅ Cleaned {cleaned_count}/{len(stuck_syncs)} stuck syncs")

        return {
            'stuck_syncs_found': len(stuck_syncs),
            'syncs_cleaned': cleaned_count
        }

    except Exception as e:
        logger.error(f"Error during stuck sync cleanup: {e}", exc_info=True)
        return {
            'stuck_syncs_found': 0,
            'syncs_cleaned': 0,
            'error': str(e)
        }


# Hook for calling from startup/scheduler
async def run_cleanup_job():
    """
    Run cleanup job (can be called from scheduler).
    """
    logger.info("🧹 Running pricing sync cleanup job")
    result = await cleanup_stuck_syncs(timeout_minutes=10)
    logger.info(f"Cleanup complete: {result}")
    return result
```

---

### Step 5: Add Cleanup to Startup (Optional but Recommended)

**File:** `src/services/startup.py`

**Add to the startup function:**

```python
async def startup():
    """Run startup tasks"""
    # ... existing startup code ...

    # Clean up any stuck syncs from previous runs
    try:
        from src.services.pricing_sync_cleanup import cleanup_stuck_syncs
        logger.info("Running startup cleanup for stuck pricing syncs...")
        result = await cleanup_stuck_syncs(timeout_minutes=5)
        logger.info(f"Startup cleanup complete: {result}")
    except Exception as e:
        logger.error(f"Error during startup cleanup: {e}")

    # ... rest of startup code ...
```

---

### Step 6: Add Scheduled Cleanup Job

**File:** `src/services/pricing_sync_scheduler.py`

**Update the scheduler loop to include cleanup:**

```python
async def _pricing_sync_scheduler_loop() -> None:
    """Main scheduler loop that runs pricing syncs and cleanup."""
    # ... existing code ...

    # Add cleanup task
    cleanup_task = asyncio.create_task(
        _cleanup_scheduler_loop(),
        name="pricing_sync_cleanup_loop"
    )

    # ... existing code ...


async def _cleanup_scheduler_loop() -> None:
    """
    Run cleanup every 15 minutes to catch stuck syncs.
    """
    from src.services.pricing_sync_cleanup import cleanup_stuck_syncs

    while not _shutdown_event.is_set():
        try:
            logger.info("🧹 Running scheduled cleanup for stuck syncs")
            result = await cleanup_stuck_syncs(timeout_minutes=10)
            logger.info(f"Scheduled cleanup complete: {result}")

        except Exception as e:
            logger.error(f"Error in cleanup scheduler: {e}", exc_info=True)

        # Wait 15 minutes
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=900)  # 15 minutes
            break  # Shutdown requested
        except asyncio.TimeoutError:
            pass  # Continue loop
```

---

## Testing the Fix

### 1. Test Async Trigger

```bash
# Trigger a sync (should return immediately)
curl -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Expected response (instant, < 1 second):
{
  "success": true,
  "sync_id": "abc123-...",
  "status": "queued",
  "message": "Pricing sync queued for background execution",
  "poll_url": "/admin/pricing/sync/abc123-..."
}
```

### 2. Poll for Status

```bash
# Poll for status (can be called multiple times)
SYNC_ID="abc123-..."

curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/sync/$SYNC_ID

# Expected responses:

# While running:
{
  "sync_id": "abc123",
  "status": "in_progress",
  "progress_percent": 50,
  "started_at": "2026-01-26T12:00:00Z",
  ...
}

# When complete:
{
  "sync_id": "abc123",
  "status": "completed",
  "progress_percent": 100,
  "started_at": "2026-01-26T12:00:00Z",
  "completed_at": "2026-01-26T12:02:30Z",
  "duration_seconds": 150.5,
  "total_models_updated": 245,
  ...
}
```

### 3. Test Cleanup

```python
# Run cleanup manually
python3 -c "
import asyncio
from src.services.pricing_sync_cleanup import cleanup_stuck_syncs

async def test():
    result = await cleanup_stuck_syncs(timeout_minutes=5)
    print(f'Cleanup result: {result}')

asyncio.run(test())
"
```

---

## Migration Checklist

- [ ] Update `src/routes/admin.py` with new async endpoint
- [ ] Add status polling endpoints to `src/routes/admin.py`
- [ ] Update `src/services/pricing_sync_scheduler.py` with queue functions
- [ ] Create `src/services/pricing_sync_cleanup.py`
- [ ] Update `src/services/startup.py` to run cleanup on startup
- [ ] Add cleanup to scheduler loop
- [ ] Test async trigger (should return instantly)
- [ ] Test status polling
- [ ] Verify no stuck syncs remain
- [ ] Update frontend to poll for status instead of waiting
- [ ] Update documentation

---

## Benefits After Fix

✅ **No more timeouts** - API returns instantly
✅ **Reliable syncs** - Syncs complete in background
✅ **No stuck syncs** - Auto-cleanup every 15 minutes
✅ **Better UX** - Users can see progress
✅ **Scalable** - Can handle long-running syncs
✅ **Resilient** - Errors don't leave orphaned records

---

## Alternative: Use Redis Queue (Advanced)

For production at scale, consider using a proper job queue:

**Option 1: Redis + RQ (Python RQ)**
```python
from rq import Queue
from redis import Redis

redis_conn = Redis.from_url(os.getenv('REDIS_URL'))
queue = Queue(connection=redis_conn)

# Queue sync
job = queue.enqueue(run_scheduled_sync, job_timeout='10m')
return {"job_id": job.id}
```

**Option 2: Celery**
```python
@celery.task(bind=True)
def pricing_sync_task(self):
    # Sync logic here
    pass

# Queue sync
task = pricing_sync_task.delay()
return {"task_id": task.id}
```

---

**Last Updated:** 2026-01-26
**Related Issue:** #958
**Priority:** P0 - Critical
