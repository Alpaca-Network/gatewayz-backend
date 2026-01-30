# Models Endpoint Cache Investigation

**Issue**: `/v1/models?gateway=all` appears to serve stale/cached data instead of fetching fresh data from provider APIs

**Date**: January 28, 2026

## Summary

The `/v1/models` endpoint uses a **two-hour stale-while-revalidate cache** that can serve cached data for up to 2 hours after the initial 1-hour fresh cache expires. This is working as designed, but may give the impression of "redirecting to hardcoded JSON" when it's actually serving stale in-memory cache.

---

## Architecture Overview

### Current Caching Strategy

There are **TWO independent caching layers**:

#### 1. In-Memory Cache (`src/cache.py`)
- **Used by**: `/v1/models` endpoint
- **Fresh TTL**: 3600 seconds (1 hour)
- **Stale TTL**: 7200 seconds (2 hours)
- **Strategy**: Stale-while-revalidate
- **Scope**: Per-instance (not shared across deployments)

#### 2. Redis Cache (`src/services/model_catalog_cache.py`)
- **Used by**: NOT currently used by `/v1/models` endpoint
- **Fresh TTL**: 900 seconds (15 minutes for full catalog)
- **Strategy**: Hard expiration (no stale serving)
- **Scope**: Distributed (shared across all instances)
- **Status**: ⚠️ **Not integrated with main endpoint**

---

## Request Flow for `/v1/models?gateway=all`

```
User Request
    ↓
GET /v1/models?gateway=all
    ↓
get_models() in catalog.py:2046
    ↓
get_cached_models("all") in models.py:772
    ↓
Check _multi_provider_catalog_cache (in-memory)
    ↓
┌─────────────────────────────────────────────────┐
│  Age < 1 hour?                                  │
│  ✅ YES → Return cached data (FRESH)           │
│  ❌ NO  → Continue                              │
└─────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────┐
│  Age < 2 hours (stale TTL)?                     │
│  ✅ YES → Return stale cache + trigger          │
│           background refresh                     │
│  ❌ NO  → Fetch fresh synchronously             │
└─────────────────────────────────────────────────┘
    ↓
_refresh_multi_provider_catalog_cache()
    ↓
_build_multi_provider_catalog()
    ↓
Call get_cached_models() for EACH provider:
  - openrouter
  - featherless
  - groq
  - fireworks
  - together
  - deepinfra
  - ... (30+ providers)
    ↓
Each provider checks its own cache:
  - openrouter: 1hr fresh / 2hr stale
  - groq: 30min fresh / 1hr stale
  - fireworks: 30min fresh / 1hr stale
  - ... etc
    ↓
If provider cache is stale/expired:
  → Call fetch_models_from_<provider>()
  → HTTP request to provider API
  → May fail/timeout → serve stale cache
    ↓
Aggregate all provider models
    ↓
Update _multi_provider_catalog_cache
    ↓
Return aggregated catalog
```

---

## Cache TTL Summary

| Cache | Fresh TTL | Stale TTL | Location |
|-------|-----------|-----------|----------|
| **Multi-provider catalog** | 1 hour | 2 hours | `src/cache.py:22` |
| OpenRouter models | 1 hour | 2 hours | `src/cache.py:12` |
| Groq models | 30 min | 1 hour | `src/cache.py:47` |
| Fireworks models | 30 min | 1 hour | `src/cache.py:54` |
| Together models | 30 min | 1 hour | `src/cache.py:61` |
| Featherless models | 1 hour | 2 hours | `src/cache.py:29` |
| DeepInfra models | 1 hour | 2 hours | `src/cache.py:78` |
| **Redis Full Catalog** | 15 min | N/A | `src/services/model_catalog_cache.py:32` |
| **Redis Provider Catalog** | 30 min | N/A | `src/services/model_catalog_cache.py:33` |

---

## Why It Appears to Serve "Hardcoded" Data

The endpoint is **NOT using hardcoded JSON files**. Here's why it appears that way:

### 1. Stale-While-Revalidate Pattern
- Cache can be up to **2 hours old** before forcing a fresh fetch
- During this window, requests get instant responses from stale cache
- Background refresh happens asynchronously (doesn't block response)

### 2. Failed API Fetches
When provider APIs fail/timeout:
```python
# models.py:1115
result = fetch_models_from_openrouter()  # May fail
_register_canonical_records("openrouter", result)
return result if result is not None else []  # Falls back to empty
```

If API fetch fails, the code:
- Returns empty list for that provider
- BUT the aggregated catalog cache still has old data
- Serves the old cached aggregated catalog

### 3. No Hardcoded Fallback
There is **NO hardcoded JSON fallback** in the code:
```bash
$ find src/services -name "*.json"
# (no results)
```

The only static JSON files are in `src/data/`:
- `fal_catalog.json` - Used only for Fal provider
- `chutes_catalog.json` - Used only for Chutes provider
- `manual_pricing.json` - Pricing data, not model catalog
- `model_capabilities.json` - Capability metadata, not catalog

None of these are used as fallback for the main catalog.

---

## Evidence from Code

### 1. Stale Cache Serving (models.py:370-373)
```python
elif cache_age < stale_ttl:
    # Stale but still usable (stale-while-revalidate)
    logger.debug(f"{provider_slug} serving stale cache (age: {cache_age:.1f}s, stale_ttl: {stale_ttl}s)")
    _register_canonical_records(provider_slug, cache["data"])
    return cache["data"]  # <-- Serves 1-2 hour old data
```

### 2. Background Revalidation (models.py:1091-1095)
```python
if cache_age < cache.get("stale_ttl", cache["ttl"]):
    revalidate_cache_in_background(
        "multi-provider-catalog", _refresh_multi_provider_catalog_cache
    )
    return cache["data"]  # <-- Returns immediately with stale data
```

### 3. Redis Cache NOT Used (model_catalog_cache.py)
```python
# This Redis cache exists but is NOT called by /v1/models endpoint
def get_cached_full_catalog() -> list[dict[str, Any]] | None:
    cache = get_model_catalog_cache()
    return cache.get_full_catalog()  # <-- NEVER CALLED by main endpoint
```

---

## Root Cause Analysis

### Why stale cache is served:

1. **Design Choice**: Stale-while-revalidate prioritizes **availability over freshness**
   - Prevents 503 errors when provider APIs are down
   - Ensures fast response times (5-20ms cached vs 500ms-2s fresh)

2. **Per-Instance Cache**: Each Railway/Vercel instance has its own cache
   - No cache sharing across instances
   - Different instances may have different cache ages

3. **Provider API Failures**: If OpenRouter/other APIs timeout/fail:
   - Background refresh fails silently
   - Stale cache continues to be served
   - Can serve 2-hour-old data until successful refresh

---

## Solutions & Recommendations

### Option 1: Reduce Stale TTL (Quick Fix)
**Change `src/cache.py:26` to reduce stale window:**
```python
_multi_provider_catalog_cache = {
    "data": [],
    "timestamp": None,
    "ttl": 3600,  # 1 hour fresh
    "stale_ttl": 3600,  # Change from 7200 to 3600 (1 hour stale, not 2)
}
```

**Pros:**
- Simple one-line change
- Reduces maximum staleness from 2 hours to 1 hour

**Cons:**
- Still serves up to 1-hour-old data
- Doesn't solve the fundamental caching issue

---

### Option 2: Integrate Redis Cache (Recommended)
**Use the existing Redis cache with 15-minute TTL:**

```python
# In models.py:1084 (where gateway == "all")
def get_cached_models(gateway: str = "openrouter"):
    if gateway == "all":
        # Try Redis cache first (15-minute TTL)
        from src.services.model_catalog_cache import get_cached_full_catalog, cache_full_catalog

        redis_cached = get_cached_full_catalog()
        if redis_cached is not None:
            logger.debug("Serving catalog from Redis cache (fresh)")
            return redis_cached

        # Fall back to in-memory cache (stale-while-revalidate)
        cache = _multi_provider_catalog_cache
        if cache.get("timestamp") is not None:
            cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
            if cache_age < 900:  # 15 minutes
                return cache["data"]

        # Build fresh catalog
        result = _refresh_multi_provider_catalog_cache()

        # Cache in Redis for next request
        cache_full_catalog(result, ttl=900)  # 15 minutes

        return result
```

**Pros:**
- Reduces staleness from 2 hours to 15 minutes
- Shares cache across all instances (consistent freshness)
- Already implemented, just needs integration

**Cons:**
- Requires Redis availability (already required for rate limiting)
- Slightly more complex logic

---

### Option 3: Add Force Refresh Parameter
**Add `?force_refresh=true` parameter:**

```python
# In catalog.py:2046
@router.get("/models", tags=["models"])
async def get_all_models(
    # ... existing parameters ...
    force_refresh: bool = Query(False, description="Force fresh data fetch, bypass cache"),
):
    if force_refresh:
        # Invalidate cache and fetch fresh
        from src.services.model_catalog_cache import invalidate_full_catalog
        invalidate_full_catalog()
        _multi_provider_catalog_cache["timestamp"] = None

    return await get_models(
        provider=provider,
        is_private=is_private,
        limit=limit,
        offset=offset,
        include_huggingface=include_huggingface,
        gateway=gateway,
    )
```

**Pros:**
- Gives control to API consumers
- Doesn't change default behavior
- Useful for debugging and admin operations

**Cons:**
- Doesn't fix the underlying staleness issue
- May be abused causing excessive API calls

---

### Option 4: Add Cache Age Header
**Expose cache age to clients:**

```python
# In catalog.py, after getting models
response = await get_models(...)
cache_age = get_cache_age(_multi_provider_catalog_cache)

# Add custom header
response_headers = {
    "X-Cache-Age": str(int(cache_age)),
    "X-Cache-Status": "HIT" if cache_age > 0 else "MISS",
    "X-Cache-Freshness": "stale" if cache_age > 3600 else "fresh"
}

return Response(
    content=json.dumps(response),
    media_type="application/json",
    headers=response_headers
)
```

**Pros:**
- Transparency for API consumers
- Helps debugging
- Doesn't change behavior

**Cons:**
- Doesn't fix the staleness issue
- Just makes it visible

---

## Recommended Implementation Plan

### Phase 1: Quick Fix (Today)
1. ✅ Reduce stale TTL from 2 hours to 1 hour
   - File: `src/cache.py:26`
   - Change: `"stale_ttl": 3600` (was 7200)

### Phase 2: Redis Integration (This Week)
2. ✅ Integrate Redis cache with 15-minute TTL
   - Modify `get_cached_models()` in `models.py:1084`
   - Use `get_cached_full_catalog()` as primary cache
   - Fall back to in-memory cache if Redis unavailable

### Phase 3: Monitoring (Next Week)
3. ✅ Add cache age headers for transparency
4. ✅ Add Prometheus metrics for cache hit/miss/age
5. ✅ Create Grafana dashboard for cache performance

### Phase 4: Optional Enhancements
6. ⚠️ Add `?force_refresh=true` parameter for admin use
7. ⚠️ Implement cache warming on deployment
8. ⚠️ Add background refresh scheduler (every 10 minutes)

---

## Testing the Current Behavior

### Check Cache Age
```bash
# Call endpoint multiple times to see if data changes
curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1" | jq '.data[0].name'

# Wait 10 seconds and call again
sleep 10
curl -s "https://api.gatewayz.ai/v1/models?gateway=all&limit=1" | jq '.data[0].name'

# If names are identical, cache is being served
```

### Check Provider API Directly
```bash
# Compare with OpenRouter direct API
curl -s "https://openrouter.ai/api/v1/models" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" | jq '.data[0].name'

# If different from Gatewayz, cache is stale
```

### Check Cache in Production
```python
# In Railway logs, look for these messages:
"serving stale cache"  # Indicates stale data being served
"Serving stale OpenRouter cache while revalidating"
"Cache HIT: Full model catalog"  # Redis cache (currently not used)
"Cache MISS: Full model catalog"
```

---

## Conclusion

The `/v1/models?gateway=all` endpoint:
- ✅ **Is NOT using hardcoded JSON** - No static files involved
- ⚠️ **Is serving stale in-memory cache** - Up to 2 hours old
- ❌ **Is NOT using Redis cache** - 15-minute cache not integrated
- ⚠️ **Background refresh may fail silently** - Stale cache persists

**Immediate action**: Reduce stale TTL to 1 hour

**Recommended solution**: Integrate Redis cache for 15-minute freshness across all instances

**Long-term**: Add monitoring, cache headers, and force refresh parameter
