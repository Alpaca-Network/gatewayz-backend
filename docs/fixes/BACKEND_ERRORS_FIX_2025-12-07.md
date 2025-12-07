# Backend Error Fixes - December 7, 2025

## Summary
Fixed critical PGRST202 error in the intelligent health monitoring service where PostgREST could not find the `update_model_tier` database function in the schema cache.

## Issues Identified

### 1. PGRST202 Error: Missing `update_model_tier` Function in Schema Cache

**Error Message:**
```
Error in tier update loop: {'code': 'PGRST202', 'details': 'Searched for the function public.update_model_tier without parameters or with a single unnamed json/jsonb parameter, but no matches were found in the schema cache.', 'hint': None, 'message': 'Could not find the function public.update_model_tier without parameters in the schema cache'}
```

**Observed In:**
- Railway deployment logs (health-service and api deployments)
- Occurs every hour when the tier update loop attempts to run
- First observed: 2025-12-07 14:54:35 PM (deployment logs)

**Root Cause:**
The `update_model_tier()` database function exists in the migrations but PostgREST's schema cache was not refreshed after the migration was applied. This caused the RPC call from the Python application to fail because PostgREST couldn't find the function in its internal cache.

**Impact:**
- Model monitoring tier updates were failing silently
- Models were not being promoted/demoted between tiers based on usage patterns
- Health monitoring continued to work, but tier optimization was non-functional
- Error logs filled with ERROR-level messages every hour

## Fixes Applied

### Code Changes

#### 1. Enhanced Error Handling in `intelligent_health_monitor.py`

**File:** `src/services/intelligent_health_monitor.py`

**Changes:**
- Added specific error handling for PGRST202 errors in the `_tier_update_loop` method
- Downgraded error from ERROR to WARNING level for missing function errors
- Added informative warning message explaining the issue and potential causes
- Used `continue` to skip the failed iteration and retry on next cycle

**Before:**
```python
async def _tier_update_loop(self):
    """Periodically update model tiers based on usage patterns"""
    while self.monitoring_active:
        try:
            await asyncio.sleep(3600)  # Every hour
            from src.config.supabase_config import supabase
            supabase.rpc("update_model_tier").execute()
            logger.info("Updated model monitoring tiers based on usage")
        except Exception as e:
            logger.error(f"Error in tier update loop: {e}", exc_info=True)
            await asyncio.sleep(3600)
```

**After:**
```python
async def _tier_update_loop(self):
    """Periodically update model tiers based on usage patterns"""
    while self.monitoring_active:
        try:
            await asyncio.sleep(3600)  # Every hour
            from src.config.supabase_config import supabase

            # Call the database function to update tiers
            try:
                supabase.rpc("update_model_tier").execute()
                logger.info("Updated model monitoring tiers based on usage")
            except Exception as rpc_error:
                # Handle database function not found or schema cache issues
                error_msg = str(rpc_error)
                if "PGRST202" in error_msg or "Could not find the function" in error_msg:
                    logger.warning(
                        f"Database function 'update_model_tier' not found in schema cache. "
                        f"This may indicate the migration hasn't been applied or PostgREST needs a schema reload. "
                        f"Error: {error_msg}"
                    )
                    # Skip this iteration and try again next hour
                    continue
                else:
                    # Re-raise other errors for proper logging
                    raise
        except Exception as e:
            logger.error(f"Error in tier update loop: {e}", exc_info=True)
            await asyncio.sleep(3600)
```

**Benefits:**
- Graceful degradation: service continues running even if function is missing
- Clear diagnostic information for debugging
- Automatic recovery when the migration is eventually applied
- Reduced error log noise

### Database Migration

#### 2. New Migration: `20251207000000_ensure_update_model_tier_function.sql`

**Purpose:** Ensure the `update_model_tier` function is available and force PostgREST to reload its schema cache.

**Key Changes:**
- Recreates the `update_model_tier` function with `SECURITY DEFINER`
- Grants execute permissions to `service_role`, `authenticated`, and `anon` roles
- Sends `NOTIFY pgrst, 'reload schema'` to force schema cache reload
- Includes a verification block that tests the function exists and is callable

**SQL Highlights:**
```sql
-- Recreate with SECURITY DEFINER for proper permissions
CREATE OR REPLACE FUNCTION public.update_model_tier()
RETURNS void
SECURITY DEFINER
AS $$
-- ... function body ...
$$ LANGUAGE plpgsql;

-- Grant permissions
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO service_role;
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO authenticated;
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO anon;

-- Force schema cache reload
NOTIFY pgrst, 'reload schema';

-- Verify function works
DO $$
BEGIN
    PERFORM public.update_model_tier();
    RAISE NOTICE 'Successfully verified update_model_tier function';
EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'Function exists but execution failed: %', SQLERRM;
END $$;
```

### Test Coverage

#### 3. New Tests in `test_intelligent_health_monitor.py`

**Tests Added:**

1. **`test_tier_update_loop_handles_missing_function()`**
   - Verifies PGRST202 errors are caught and logged as warnings
   - Confirms the service continues running after the error
   - Validates the warning message contains diagnostic information

2. **`test_tier_update_loop_handles_other_errors()`**
   - Ensures other errors (network timeouts, etc.) are still logged as errors
   - Confirms error handling doesn't suppress legitimate errors

**Coverage:**
- Error handling logic: ✅
- PGRST202 specific handling: ✅
- Other error types: ✅
- Warning message content: ✅

## Verification Steps

### 1. Check Migration Applied

```bash
# Connect to Supabase and verify function exists
supabase db functions list | grep update_model_tier

# Or check via SQL
SELECT routine_name, routine_type
FROM information_schema.routines
WHERE routine_schema = 'public'
AND routine_name = 'update_model_tier';
```

### 2. Test RPC Call

```bash
# Via Supabase client
supabase db rpc update_model_tier

# Or via psql
SELECT update_model_tier();
```

### 3. Check Railway Logs

After deploying the fix, verify:
```bash
railway logs --service api | grep "tier update"
```

**Expected Output:**
- If migration applied: `"Updated model monitoring tiers based on usage"`
- If migration not yet applied: `"Database function 'update_model_tier' not found in schema cache"` (WARNING level, not ERROR)

### 4. Run Tests

```bash
# Run the specific new tests
pytest tests/test_intelligent_health_monitor.py::test_tier_update_loop_handles_missing_function -xvs
pytest tests/test_intelligent_health_monitor.py::test_tier_update_loop_handles_other_errors -xvs

# Run all health monitor tests
pytest tests/test_intelligent_health_monitor.py -xvs
```

## Deployment Checklist

- [x] Code changes to error handling
- [x] Database migration created
- [x] Test coverage added
- [x] Documentation written
- [ ] Code reviewed
- [ ] Tests passing in CI
- [ ] Migration applied to staging database
- [ ] Verified in staging environment
- [ ] Migration applied to production database
- [ ] Deployed to production
- [ ] Verified in production logs

## Rollback Plan

If issues occur after deployment:

### Code Rollback
```bash
git revert <commit-hash>
```

### Database Rollback
The migration is non-destructive (only recreates existing function), so rollback is not necessary. However, if needed:
```sql
-- The function will continue to exist from previous migrations
-- No action required unless permissions need adjustment
```

## Related Issues

- **Previous Fixes:** PR #570 - Fixed health-service ModuleNotFoundError
- **Related Migrations:**
  - `20251128000000_enhance_model_health_tracking.sql` - Original function creation
  - `20251205000000_restore_model_health_tracking.sql` - Function restoration
  - `20251205000001_fix_model_health_tracking_issues.sql` - Function optimization

## Monitoring

### Key Metrics to Watch

1. **Error Rate:** Monitor for `"Error in tier update loop"` in logs
   - **Before fix:** 1 ERROR/hour
   - **After fix (migration not applied):** 1 WARNING/hour
   - **After fix (migration applied):** 0 errors, 1 INFO/hour

2. **Model Tier Distribution:** Check `model_health_tracking.monitoring_tier`
   - Should see models moving between tiers based on usage

3. **Health Service Uptime:** Should remain 100%

### Alerting

Set up alerts for:
- Multiple consecutive warnings about missing function (indicates migration wasn't applied)
- Sudden increase in tier update errors
- Models stuck in wrong tiers

## Notes

- The error was occurring every hour but was non-fatal
- Health monitoring continued to function normally
- The fix ensures graceful degradation until migration is applied
- Added comprehensive test coverage for this error scenario

## Author
- **Date:** 2025-12-07
- **Context:** Routine backend error investigation
- **Severity:** Medium (non-fatal but filling error logs)

## References
- Railway deployment logs: `api` service, 2025-12-07 14:54:35
- Supabase PostgREST error codes: https://postgrest.org/en/stable/errors.html
- Migration files: `supabase/migrations/2025120*`
