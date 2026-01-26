# Implementation Guide: Pricing Scheduler Performance Fixes

**Issue**: #959 - Pricing scheduler performance and resource usage
**Status**: ⚠️ CRITICAL FIXES IMPLEMENTED
**Date**: January 26, 2026

---

## Overview

This guide documents the implementation of P0 (immediate priority) fixes to resolve the critical performance issues identified in the pricing scheduler testing:

- **70% failure rate** under load (502/504 errors)
- **API response times 7-21x over target** (37-108s vs 5s)
- **Inconsistent performance** (3x variance in sync duration)

---

## What Was Fixed

### ✅ Fix #1: Distributed Locking (P0)

**Problem**: Multiple concurrent pricing syncs cause 502/504 errors and server overload.

**Solution**: Implemented database-backed distributed locking system.

#### Files Created/Modified:

1. **Database Migration** (`supabase/migrations/20260126000001_add_pricing_sync_tables.sql`)
   - Added `pricing_sync_lock` table
   - Added `cleanup_expired_pricing_locks()` function
   - Automatic lock expiry prevents stale locks

2. **Python Service** (`src/services/pricing_sync_lock.py`)
   - `PricingSyncLock` class for distributed locking
   - Context manager API for easy usage
   - Automatic lock cleanup
   - Lock status checking functions

3. **Admin Endpoint** (`src/routes/admin.py`)
   - Updated `/admin/pricing/scheduler/trigger` to use distributed lock
   - Returns 429 error if sync already in progress
   - Includes lock info in error response

#### How It Works:

```python
# Before (no locking - multiple concurrent syncs possible)
result = await trigger_manual_sync()

# After (with distributed locking)
async with pricing_sync_lock(
    lock_key="pricing_sync_global",
    timeout_seconds=300,
    request_id=f"admin_{admin_user.get('id')}"
):
    result = await trigger_manual_sync()
```

**Benefits**:
- ✅ Prevents concurrent syncs that cause 502/504 errors
- ✅ Returns clear 429 error with lock info when sync in progress
- ✅ Automatic lock expiry (5 minutes) prevents permanent locks
- ✅ Tracks who acquired the lock for debugging

---

## Deployment Steps

### Step 1: Run Database Migration

```bash
# Connect to Supabase and run migration
cd /path/to/gatewayz-backend

# Option A: Using Supabase CLI
supabase db push

# Option B: Manually via Supabase dashboard
# Copy contents of supabase/migrations/20260126000001_add_pricing_sync_tables.sql
# Run in SQL Editor
```

**Verify Migration**:
```sql
-- Check tables created
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('pricing_sync_lock', 'model_pricing_history', 'pricing_sync_log');

-- Should return 3 rows
```

### Step 2: Deploy Code Changes

```bash
# 1. Commit changes
git add supabase/migrations/20260126000001_add_pricing_sync_tables.sql
git add src/services/pricing_sync_lock.py
git add src/routes/admin.py
git commit -m "fix: add distributed locking for pricing sync to prevent 502/504 errors

- Add pricing_sync_lock table with auto-expiry
- Implement PricingSyncLock service with context manager API
- Update admin trigger endpoint to use distributed lock
- Return 429 when sync already in progress

Fixes #959"

# 2. Push to staging
git push origin staging

# 3. Deploy to Railway staging
railway up --environment staging

# Or if using auto-deploy:
# Railway will auto-deploy from staging branch
```

### Step 3: Test the Fix

```bash
# Set admin key
export STAGING_ADMIN_KEY="your-admin-key"

# Test 1: Single sync should work
curl -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Test 2: Immediate second sync should return 429
curl -X POST \
  -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/trigger

# Expected: 429 with message "Pricing sync already in progress"

# Test 3: Run automated performance test
./scripts/test_pricing_scheduler_performance.sh
```

### Step 4: Monitor and Validate

```bash
# Check lock status
curl -H "Authorization: Bearer $STAGING_ADMIN_KEY" \
  https://gatewayz-staging.up.railway.app/admin/pricing/scheduler/status

# Check sync logs in database
# Run in Supabase SQL Editor:
SELECT * FROM pricing_sync_log
ORDER BY sync_started_at DESC
LIMIT 10;

# Check active locks
SELECT * FROM pricing_sync_lock;
```

---

## Additional Recommended Improvements (Not Yet Implemented)

### P1: Increase Railway Timeout (RECOMMENDED)

**Current**: Railway/proxy timeout appears to be ~60-120 seconds
**Recommended**: 180-240 seconds

#### How to Configure:

1. **Via Railway Dashboard**:
   ```
   1. Go to Railway project settings
   2. Select the service
   3. Navigate to "Settings" → "Deploy"
   4. Set "Health Check Timeout" to 240 seconds
   5. Set "Health Check Interval" to 30 seconds
   6. Save and redeploy
   ```

2. **Via railway.json** (if supported):
   ```json
   {
     "$schema": "https://railway.app/railway.schema.json",
     "build": {
       "builder": "NIXPACKS"
     },
     "deploy": {
       "healthcheckTimeout": 240,
       "healthcheckInterval": 30,
       "restartPolicyType": "ON_FAILURE",
       "restartPolicyMaxRetries": 10
     }
   }
   ```

### P1: Convert to Async Endpoint (RECOMMENDED)

**Why**: Current endpoint blocks for 30-60 seconds, poor UX

**How**: Implement background job processing:

```python
# Create new endpoint pattern:
@router.post("/admin/pricing/scheduler/trigger")
async def trigger_manual_pricing_sync_async(
    background_tasks: BackgroundTasks,
    admin_user: dict = Depends(require_admin)
):
    """Returns immediately with job ID"""
    job_id = str(uuid.uuid4())

    # Queue background task
    background_tasks.add_task(
        run_pricing_sync_with_job_id,
        job_id=job_id,
        admin_user=admin_user
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "check_status_url": f"/admin/pricing/scheduler/job/{job_id}"
    }

@router.get("/admin/pricing/scheduler/job/{job_id}")
async def get_sync_job_status(job_id: str):
    """Check job status"""
    # Query pricing_sync_log table by job_id
    pass
```

### P2: Progressive Provider Sync (OPTIMIZATION)

**Why**: Sync all providers sequentially reduces variance

**How**: Implement provider batching:

```python
# Instead of syncing all providers at once
async def sync_providers_progressive(providers: list):
    for provider in providers:
        try:
            await sync_single_provider(provider)
            # Report progress
            await update_sync_progress(provider, "completed")
        except Exception as e:
            # Continue with next provider
            logger.error(f"Provider {provider} failed: {e}")
            await update_sync_progress(provider, "failed")
```

---

## Expected Improvements After Fix

### Before Fix (Current State):

| Metric | Value | Status |
|--------|-------|--------|
| Load Test Success Rate | 30% (3/10) | ❌ FAIL |
| API Response Time | 37-108s | ❌ FAIL |
| 502/504 Errors | 70% of requests | ❌ FAIL |

### After Fix #1 (Distributed Locking):

| Metric | Expected Value | Status |
|--------|---------------|--------|
| Load Test Success Rate | **100% (sequential)** | ✅ EXPECTED PASS |
| API Response Time | 37-60s (unchanged) | ⚠️ Still over target |
| 502/504 Errors | **0% (prevented by lock)** | ✅ EXPECTED PASS |
| Concurrent Sync Attempts | **429 error (graceful)** | ✅ EXPECTED PASS |

### After All P1 Fixes:

| Metric | Expected Value | Status |
|--------|---------------|--------|
| Load Test Success Rate | **100%** | ✅ PASS |
| API Response Time | **< 5s (async)** | ✅ PASS |
| Sync Duration | 30-60s (background) | ✅ PASS |
| User Experience | **Immediate response** | ✅ PASS |

---

## Testing Checklist

After deployment, verify:

- [ ] Database migration successful (3 tables created)
- [ ] Single manual sync works (returns 200)
- [ ] Immediate second sync returns 429 with lock info
- [ ] Lock expires after 5 minutes (300 seconds)
- [ ] Expired locks are cleaned up automatically
- [ ] Sync logs are written to `pricing_sync_log` table
- [ ] No 502/504 errors during consecutive triggers
- [ ] Performance test shows improved success rate
- [ ] Railway logs show lock acquisition/release messages

---

## Rollback Plan

If issues occur after deployment:

### Option 1: Revert Code Changes

```bash
# Revert to previous commit
git revert HEAD
git push origin staging
railway up --environment staging
```

### Option 2: Disable Lock (Emergency)

```python
# In src/routes/admin.py, temporarily bypass lock:
async def trigger_manual_pricing_sync(admin_user: dict = Depends(require_admin)):
    # Comment out lock context manager
    # async with pricing_sync_lock(...):

    # Direct call (old behavior)
    result = await trigger_manual_sync()
    return result
```

### Option 3: Database Rollback

```sql
-- Remove lock table (if causing issues)
DROP TABLE IF EXISTS pricing_sync_lock CASCADE;
DROP FUNCTION IF EXISTS cleanup_expired_pricing_locks();
```

---

## Monitoring and Alerts

### Key Metrics to Monitor:

1. **Sync Success Rate**
   ```sql
   SELECT
       COUNT(*) FILTER (WHERE status = 'success') * 100.0 / COUNT(*) as success_rate,
       COUNT(*) as total_syncs
   FROM pricing_sync_log
   WHERE sync_started_at > NOW() - INTERVAL '24 hours';
   ```

2. **Lock Contention**
   ```sql
   -- Check how often lock acquisition fails
   -- (Monitor 429 errors in application logs)
   SELECT COUNT(*) FROM pricing_sync_lock;
   -- Should be 0 or 1 (one active lock max)
   ```

3. **Sync Duration Trends**
   ```sql
   SELECT
       DATE_TRUNC('hour', sync_started_at) as hour,
       AVG(duration_ms) / 1000.0 as avg_duration_seconds,
       MAX(duration_ms) / 1000.0 as max_duration_seconds
   FROM pricing_sync_log
   WHERE sync_started_at > NOW() - INTERVAL '7 days'
   GROUP BY hour
   ORDER BY hour DESC;
   ```

### Recommended Alerts:

1. **Sync Failure Alert**
   - Trigger: 3+ consecutive failed syncs
   - Action: Investigate provider API issues

2. **Long-Running Sync Alert**
   - Trigger: Sync duration > 120 seconds
   - Action: Check for slow provider APIs

3. **Stale Lock Alert**
   - Trigger: Lock older than 10 minutes
   - Action: Manual cleanup may be needed

---

## FAQ

### Q: What happens if a lock expires during a sync?

**A**: The lock expiry is set to 5 minutes (300 seconds), which is longer than the typical sync duration (30-60s). If a sync takes longer than 5 minutes, the lock will expire and another sync could start. To prevent this:
- Monitor sync duration and increase lock timeout if needed
- Optimize slow provider syncs

### Q: Can I run syncs for different providers concurrently?

**A**: Currently, no. The global lock prevents all syncs. For provider-specific locking:
```python
async with pricing_sync_lock(lock_key=f"pricing_sync_{provider}"):
    await sync_provider(provider)
```

### Q: What if the lock table gets corrupted?

**A**: The system will fall back to allowing concurrent syncs (old behavior). You'll see database errors in logs. Fix:
```sql
TRUNCATE pricing_sync_lock;
```

### Q: How do I manually release a stuck lock?

**A**:
```sql
-- Check locks
SELECT * FROM pricing_sync_lock;

-- Release specific lock
DELETE FROM pricing_sync_lock WHERE id = 123;

-- Release all locks (use with caution)
TRUNCATE pricing_sync_lock;
```

---

## Related Documentation

- Performance Test Results: `pricing_scheduler_performance_findings.md`
- Test Script: `scripts/test_pricing_scheduler_performance.sh`
- GitHub Issue: #959
- Migration File: `supabase/migrations/20260126000001_add_pricing_sync_tables.sql`

---

## Summary

**Status**: ✅ P0 FIX IMPLEMENTED (Distributed Locking)

**Immediate Impact**:
- Prevents 502/504 errors from concurrent syncs
- Returns graceful 429 error when sync in progress
- Automatic lock cleanup prevents stale locks

**Next Steps**:
1. Deploy and test in staging ✅ (ready)
2. Increase Railway timeout (recommended)
3. Convert to async endpoint (recommended)
4. Monitor performance improvements
5. Deploy to production once validated

**Deployment Risk**: 🟡 LOW-MEDIUM
- Non-breaking change (backward compatible)
- Graceful degradation if lock system fails
- Easy rollback plan

**Recommendation**: ✅ READY TO DEPLOY TO STAGING
