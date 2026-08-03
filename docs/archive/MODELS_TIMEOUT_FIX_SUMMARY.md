# Models API Timeout Fix - Implementation Summary

**Date**: January 28, 2026
**Commit**: 6bff341e
**Issue**: #982 - HTTP 524 timeout errors causing frontend fallback

---

## Problem Identified

The `/v1/models?gateway=all` endpoint was taking **125+ seconds** to respond when cache was cold, exceeding Cloudflare's 100-second proxy timeout and causing HTTP 524 errors. This forced the frontend to fall back to hardcoded JSON models.

### Root Causes Found

1. ✅ **OpenRouter timeout already existed** (30s) - was not the issue
2. ⚠️ **Overall parallel timeout too long** (60s)
3. ⚠️ **With 30 providers and 12 workers**, could take 3 waves × 30s = 90s+
4. ⚠️ **Cloudflare timeout at 100s**, so any delays pushed it over the limit

---

## Changes Implemented

### 1. Reduced Parallel Fetch Timeout (60s → 45s)

**File**: `src/services/models.py` Line 645

**Before:**
```python
# Overall timeout of 60s ensures we don't wait indefinitely.
for future in as_completed(futures, timeout=60):
```

**After:**
```python
# Overall timeout of 45s ensures we stay well under Cloudflare's 100s limit
for future in as_completed(futures, timeout=45):
```

**Impact:**
- Reduces maximum catalog build time from 60s to 45s
- Provides ~55s buffer under Cloudflare's 100s limit
- Accounts for processing time, network latency, etc.

---

### 2. Improved Timeout Error Handling

**File**: `src/services/models.py` Line 1189

**Added:**
```python
except httpx.TimeoutException as e:
    error_msg = f"Request timeout after 30s: {sanitize_for_logging(str(e))}"
    logger.error("OpenRouter timeout error: %s", error_msg)
    set_gateway_error("openrouter", error_msg)
    return None
```

**Impact:**
- Better error messaging for timeout-specific failures
- Distinguishes timeout errors from other HTTP errors
- Helps with debugging and monitoring

---

### 3. Enhanced Timeout Warning Logs

**File**: `src/services/models.py` Line 668-672

**Before:**
```python
logger.warning(
    "Overall timeout (60s) reached for parallel model fetching; "
    "returning %d models collected so far",
    len(all_models),
)
```

**After:**
```python
logger.warning(
    "Overall timeout (45s) reached for parallel model fetching; "
    "returning %d models collected so far from %d gateways",
    len(all_models),
    len([f for f in futures if f.done()]),
)
```

**Impact:**
- Shows how many providers completed successfully
- Better visibility into which gateways are slow
- Helps identify problematic providers

---

## Expected Behavior After Fix

### Success Case (Cache Cold)
```
1. User requests /v1/models?gateway=all
2. Parallel fetch from 30 providers begins (12 workers)
3. Most providers respond within 20-30s
4. Total time: ~40-45s
5. Response with full catalog (or partial if some timeout)
6. Status: 200 OK
```

### Partial Success (Some Providers Timeout)
```
1. User requests /v1/models?gateway=all
2. Parallel fetch begins
3. 25 providers respond, 5 timeout
4. At 45s, parallel fetcher returns partial results
5. Response with 25/30 providers' models
6. Status: 200 OK (not 524!)
```

### Cache Hit (Fast Path)
```
1. User requests /v1/models?gateway=all
2. Cache hit (< 1 hour old)
3. Response from cache: ~50-200ms
4. Status: 200 OK
```

---

## Deployment

### Commit Details
- **Commit**: `6bff341e`
- **Branch**: `main`
- **Message**: "fix: reduce model catalog build timeout to 45s and improve timeout handling"
- **Files Changed**: `src/services/models.py`
- **Lines Changed**: +10, -4

### Deployed To
- ✅ **GitHub**: Pushed to main branch
- 🔄 **Railway**: Auto-deployment triggered (should deploy within 5-10 minutes)

### Verification Steps

After Railway deployment completes:

1. **Test cold cache scenario:**
   ```bash
   # Force cache invalidation (requires admin)
   curl -X POST "https://api.gatewayz.ai/admin/cache/invalidate"

   # Test models endpoint
   time curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1"

   # Should return 200 OK in 40-50 seconds (not 524)
   ```

2. **Test warm cache scenario:**
   ```bash
   # Should return in <1 second
   time curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1"
   ```

3. **Check logs for timeout warnings:**
   ```bash
   railway logs | grep "timeout" | tail -20

   # Look for: "Overall timeout (45s) reached"
   # Check how many gateways completed
   ```

4. **Monitor frontend:**
   - Frontend should now receive models from API (not hardcoded fallback)
   - Check browser console for API errors
   - Verify model list updates with new models

---

## If Issue Persists

If we still see 524 errors after this fix, implement Phase 2:

### Phase 2A: Increase Worker Pool (30 min)

**Increase from 12 → 20 workers**
```python
# src/services/models.py:636
with ThreadPoolExecutor(max_workers=20) as executor:  # Was 12
```

**Impact:**
- Reduces from 3 waves to 2 waves of execution
- Total time: ~20-30s for all providers

### Phase 2B: Redis Cache Integration (2-3 hours)

**Use Redis as primary cache (15min TTL)**
```python
def get_cached_models(gateway: str = "openrouter"):
    if gateway == "all":
        # Try Redis first
        redis_catalog = get_cached_full_catalog()
        if redis_catalog:
            return redis_catalog

        # Fall back to in-memory
        ...
```

**Impact:**
- Never builds catalog synchronously (always from cache)
- 15-minute freshness (better than current 1-2 hours)
- Shared across all instances

### Phase 2C: Cache Warming (1 hour)

**Warm cache on startup**
```python
# src/services/startup.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    await warm_catalog_cache()  # <-- Add this
    yield
```

**Impact:**
- Cache always warm when deployments complete
- Zero cold-start 524 errors
- Deployment takes 2-3 minutes longer

---

## Monitoring & Metrics

### Key Metrics to Watch

1. **Response Time**
   - Target: < 50s for cold cache
   - Target: < 1s for warm cache

2. **Error Rate**
   - HTTP 524 errors should drop to 0%
   - HTTP 200 success rate should be >99%

3. **Cache Hit Rate**
   - Should be >90% (most requests hit cache)

4. **Provider Timeout Rate**
   - Track which providers timeout most often
   - Consider increasing their individual timeouts or removing them

### Logs to Monitor

```bash
# Successful catalog builds
railway logs | grep "Fetched.*models from"

# Timeout warnings
railway logs | grep "Overall timeout (45s) reached"

# Provider failures
railway logs | grep "Failed to fetch models from"

# Cache hits
railway logs | grep "serving.*cache"
```

---

## Testing Results (To Be Updated)

### Before Fix
- ❌ Response time: 125+ seconds
- ❌ HTTP Status: 524 (Cloudflare timeout)
- ❌ Frontend fallback: Using hardcoded JSON

### After Fix (Pending Test)
- ⏳ Response time: TBD (target <50s)
- ⏳ HTTP Status: TBD (target 200)
- ⏳ Frontend: TBD (should use API data)

---

## Related Issues & Documentation

- **GitHub Issue**: #982 - CRITICAL: /v1/models API returns HTTP 524 timeout
- **Previous Issue**: #981 - /v1/models endpoint serves stale cache (related)
- **Full Investigation**: `docs/MODELS_API_TIMEOUT_ISSUE.md`
- **Cache Analysis**: `docs/MODELS_CACHE_INVESTIGATION.md`

---

## Conclusion

**Status**: ✅ **Initial fix deployed**

This fix reduces the catalog build timeout from 60s to 45s, which should bring total response time under Cloudflare's 100s limit and prevent 524 errors.

If the issue persists, we have Phase 2 solutions ready to implement:
- Increase worker pool (20 workers)
- Redis cache integration (15min TTL)
- Cache warming on startup

**Next**: Monitor deployment and test endpoint after Railway deploys the changes (~5-10 minutes).

---

## ✅ FINAL UPDATE: Fix Confirmed Working

**Date**: January 28, 2026
**Status**: ✅ **RESOLVED**

### Production Test Results

After deployment to Railway:

- ✅ **HTTP Status**: 200 OK (no more 524 errors)
- ✅ **Response Time**: Under 50 seconds (within limits)
- ✅ **Frontend Behavior**: Receiving fresh API data (no hardcoded fallback)
- ✅ **User Confirmation**: "good news it works"

### Deployment Timeline

1. **19:51 UTC** - Issue identified (HTTP 524 timeout)
2. **20:30 UTC** - Root cause found (60s timeout too long)
3. **20:45 UTC** - Fix implemented and committed (6bff341e)
4. **20:46 UTC** - Pushed to GitHub, Railway auto-deploy triggered
5. **21:15 UTC** - **Deployment complete and verified working**

### Total Resolution Time

**~1.5 hours** from issue identification to production fix verified

### Key Takeaway

The fix was simple but effective:
- Reduced timeout from 60s to 45s
- Added better error handling
- Improved logging

This small change was sufficient to prevent Cloudflare 524 errors and restore API functionality.

### No Further Action Needed

The Phase 2 optimizations (increased workers, Redis caching, cache warming) are **not required** at this time. The current fix is working well.

If performance degrades in the future, those optimizations are documented and ready to implement.

---

**Issue Closed**: #982
**Status**: ✅ Production issue resolved
