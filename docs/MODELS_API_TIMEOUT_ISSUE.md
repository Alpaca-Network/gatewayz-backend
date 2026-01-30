# CRITICAL: /v1/models API Timeout Issue (HTTP 524)

**Date**: January 28, 2026
**Severity**: 🔴 **CRITICAL** - API is non-functional
**Status**: Production outage

---

## Problem Statement

**You were RIGHT!** The issue is NOT just stale caching. The **API is timing out completely** when the cache is cold, returning HTTP 524 errors, which causes the frontend to fall back to hardcoded JSON models.

## Symptoms

1. ❌ **All `/v1/models` requests timeout** after 10-15 seconds
2. ❌ **HTTP 524 error** from Cloudflare (timeout occurred)
3. ❌ **Response time**: 125+ seconds (exceeds Cloudflare's 100s timeout)
4. ⚠️ **Frontend fallback**: Uses hardcoded JSON because API is unreachable
5. ⚠️ **ALL endpoints affected**: Even `/health` and `/` timeout

## Root Cause

### HTTP 524: A Timeout Occurred

```bash
$ curl "https://api.gatewayz.ai/v1/models?gateway=all"
error code: 524
HTTP Status: 524
Time: 125.208179s  # <-- 2+ minutes!
```

**Cloudflare Error 524** means:
> "Cloudflare was able to complete a TCP connection to the origin server, but did not receive a timely HTTP response."

Cloudflare's proxy timeout is **100 seconds**. The API is taking **125+ seconds**, causing Cloudflare to terminate the connection.

### Why It's So Slow

When cache is empty/expired, `_build_multi_provider_catalog()` does:

```python
# Sequentially fetch from 30+ providers:
for gateway in ['openrouter', 'featherless', 'groq', 'fireworks', ...]:
    models = get_cached_models(gateway)  # Each may take 3-10s
    all_models.extend(models)
```

**Bottleneck**:
- 30+ provider API calls (some sequential, some fail/timeout)
- Featherless alone returns 16,546 models (takes 3-4 seconds)
- Total time: 30 providers × ~4s avg = **120+ seconds**
- Exceeds Cloudflare's 100-second timeout
- Cache was designed to prevent this, but when cache is cold/expired, API becomes unusable

### Evidence from Logs

```
2026-01-28 19:51:09 [INFO] GET /v1/models
2026-01-28 19:51:09 [INFO] Fetching models from Infron AI API...
2026-01-28 19:51:09 [ERRO] Failed to fetch models from Infron AI: ConnectError
2026-01-28 19:51:09 [INFO] Fetching all models from Featherless API
2026-01-28 19:51:13 [INFO] Fetched 16546 models from Featherless  # <-- 4 seconds!
... (30+ more providers)
```

Fetching models from all providers takes well over 100 seconds, causing 524.

---

## Impact Assessment

| Impact | Severity | Details |
|--------|----------|---------|
| **API Availability** | 🔴 Critical | `/v1/models` endpoint completely non-functional |
| **Frontend** | 🔴 Critical | Falls back to hardcoded JSON (stale data) |
| **User Experience** | 🔴 Critical | Users see outdated model list |
| **Catalog Updates** | 🔴 Critical | New models don't appear in UI |
| **Cache Dependency** | 🔴 Critical | API only works when cache is warm |

**Production Status**: 🔴 **OUTAGE** - API cannot serve fresh catalog data

---

## Timeline of Events

1. **Cache expires** (after 1-2 hours)
2. **User requests** `/v1/models?gateway=all`
3. **Cache miss** triggers `_build_multi_provider_catalog()`
4. **Sequential fetching** from 30+ providers begins
5. **Fetching takes 120+ seconds**
6. **Cloudflare times out** at 100 seconds → HTTP 524
7. **Frontend receives 524 error**
8. **Frontend falls back** to hardcoded JSON models
9. **Users see stale/incomplete data**

---

## Solutions

### 🚨 Immediate Fix (Emergency - 30 minutes)

**Option A: Increase Cloudflare Timeout**
- Contact Cloudflare support to increase proxy timeout from 100s to 600s
- **Pros**: Allows current slow build to complete
- **Cons**: Doesn't fix the underlying performance issue, 600s is still too slow

**Option B: Deploy Cache Warming on Startup**
```python
# In src/services/startup.py - lifespan events
async def warm_catalog_cache():
    """Warm the model catalog cache on startup/deployment"""
    logger.info("Warming model catalog cache...")
    try:
        # Build catalog in background
        result = await asyncio.to_thread(_refresh_multi_provider_catalog_cache)
        logger.info(f"Cache warmed with {len(result)} models")
    except Exception as e:
        logger.error(f"Cache warming failed: {e}")

# Add to lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm cache on startup
    await warm_catalog_cache()
    yield
    # Cleanup on shutdown
```

**Pros**: Cache is always warm when API starts, prevents cold start 524s
**Cons**: Deployment takes 2+ minutes longer

---

### ✅ Short-term Fix (1-2 hours)

**Implement Parallel Fetching with Timeout Protection**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def _build_multi_provider_catalog_parallel():
    """Build catalog with parallel provider fetches and timeout protection"""

    gateways = [
        'openrouter', 'featherless', 'groq', 'fireworks', 'together',
        'deepinfra', 'cerebras', 'xai', 'novita', 'hug', 'aimo',
        # ... all 30+ gateways
    ]

    async def fetch_with_timeout(gateway: str, timeout: float = 10.0):
        """Fetch models from gateway with timeout"""
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, get_cached_models, gateway),
                timeout=timeout
            )
            return gateway, result or []
        except asyncio.TimeoutError:
            logger.warning(f"Gateway {gateway} timed out after {timeout}s, skipping")
            return gateway, []
        except Exception as e:
            logger.error(f"Gateway {gateway} failed: {e}")
            return gateway, []

    # Fetch from all gateways in parallel with 10s timeout each
    tasks = [fetch_with_timeout(gw, timeout=10.0) for gw in gateways]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Aggregate results
    all_models = []
    for gateway, models in results:
        if isinstance(models, list):
            all_models.extend(models)
            logger.info(f"✅ {gateway}: {len(models)} models")

    # Deduplicate and build canonical registry
    # ... (existing logic)

    return all_models
```

**Benefits**:
- Fetches from 30 providers in parallel (not sequential)
- Total time: ~10-15 seconds (timeout per provider) instead of 120s
- Stays well under Cloudflare's 100s timeout
- Failed/slow providers don't block the entire response

**Effort**: 1-2 hours to implement and test

---

### ✅ Medium-term Fix (This Week)

**Use Redis Cache as Primary with Background Refresh**

```python
def get_cached_models(gateway: str = "openrouter"):
    if gateway == "all":
        # 1. Try Redis cache first (15-minute TTL)
        from src.services.model_catalog_cache import get_cached_full_catalog

        redis_catalog = get_cached_full_catalog()
        if redis_catalog is not None:
            logger.info("Serving catalog from Redis cache (fast path)")
            return redis_catalog

        # 2. Try in-memory cache (even if stale)
        cache = _multi_provider_catalog_cache
        if cache.get("data") and cache.get("timestamp"):
            cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()

            # Serve stale cache immediately
            logger.info(f"Serving stale in-memory cache ({cache_age:.0f}s old)")

            # Trigger async refresh in background
            if cache_age > 900:  # Older than 15 minutes
                asyncio.create_task(refresh_catalog_async())

            return cache["data"]

        # 3. No cache available - must build synchronously (slow path)
        logger.warning("No cache available, building catalog (slow!)")
        return _refresh_multi_provider_catalog_cache()
```

**Benefits**:
- Never serves slow response (always return cached data)
- Async background refresh doesn't block requests
- Redis cache shared across all instances

---

### ✅ Long-term Fix (Next Sprint)

**Background Refresh Scheduler**

```python
# In src/services/startup.py

async def catalog_refresh_scheduler():
    """Refresh catalog every 10 minutes in background"""
    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
            logger.info("Background catalog refresh starting...")

            result = await asyncio.to_thread(_build_multi_provider_catalog_parallel)

            # Update both caches
            _multi_provider_catalog_cache["data"] = result
            _multi_provider_catalog_cache["timestamp"] = datetime.now(timezone.utc)

            from src.services.model_catalog_cache import cache_full_catalog
            cache_full_catalog(result, ttl=900)

            logger.info(f"Background refresh complete: {len(result)} models")

        except Exception as e:
            logger.error(f"Background refresh failed: {e}")

# Start scheduler on app startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background refresh task
    refresh_task = asyncio.create_task(catalog_refresh_scheduler())

    yield

    # Cancel on shutdown
    refresh_task.cancel()
```

**Benefits**:
- Cache never expires (always fresh)
- No user request triggers slow catalog build
- Catalog updates every 10 minutes automatically

---

## Recommended Implementation Order

### Phase 1: Emergency (TODAY - 2 hours)
1. ✅ **Deploy cache warming on startup** (30 min)
   - Ensures cache is warm when Railway deploys

2. ✅ **Implement parallel fetching** (1.5 hours)
   - Reduces cold start time from 120s → 15s
   - Prevents 524 errors

### Phase 2: Short-term (This Week - 4 hours)
3. ✅ **Integrate Redis cache as primary** (2 hours)
   - Always serve from cache (never build synchronously)
   - Background async refresh

4. ✅ **Add monitoring & alerts** (2 hours)
   - Prometheus metrics for catalog build time
   - Alert if build takes > 30s
   - Alert if Redis cache is empty

### Phase 3: Long-term (Next Sprint - 6 hours)
5. ✅ **Implement background refresh scheduler** (3 hours)
   - Refresh catalog every 10 minutes
   - Never let cache go stale

6. ✅ **Add cache warming webhook** (2 hours)
   - Trigger refresh when providers add new models
   - Manual admin endpoint to force refresh

7. ✅ **Performance optimization** (1 hour)
   - Profile slow providers (Featherless: 16k models)
   - Consider provider-specific TTLs

---

## Testing

### Verify the Issue
```bash
# Test current slow behavior
time curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1"
# Should return 524 after 100-125 seconds

# Check if cache is cold
railway logs | grep "Building multi-provider catalog"
```

### After Parallel Fetching Fix
```bash
# Should return in <30 seconds (under Cloudflare timeout)
time curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1"

# Check logs for parallel fetching
railway logs | grep "✅" | grep "models"
```

### After Redis Integration
```bash
# Should return in <1 second (Redis cache hit)
time curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1"

# Check Redis cache status
redis-cli GET models:catalog:full
```

---

## Key Files to Modify

1. **`src/services/models.py:772-1097`**
   - `get_cached_models()` function
   - Add parallel fetching logic
   - Integrate Redis cache

2. **`src/services/startup.py`**
   - Add cache warming on startup
   - Add background refresh scheduler

3. **`src/cache.py:22-27`**
   - Adjust TTL values (secondary priority)

4. **`src/services/model_catalog_cache.py`**
   - Already implemented, just needs integration

---

## Conclusion

**The frontend is falling back to hardcoded JSON because**:
1. ❌ API times out with HTTP 524 error (Cloudflare 100s limit)
2. ❌ Catalog build takes 120+ seconds (30+ sequential API calls)
3. ❌ Happens whenever cache is cold/expired
4. ✅ **NOT a caching issue** - it's a **performance/timeout issue**

**Immediate action needed**: Implement parallel fetching to reduce build time from 120s → 15s

**My previous analysis was incomplete**: I focused on cache staleness (2 hours) but missed that the API **completely fails** when cache expires, causing 524 errors and frontend fallback.

You were right to question it! ✅
