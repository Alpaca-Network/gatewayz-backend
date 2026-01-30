# Redis Cache Explained - Why Your Web App Shows Stale Data

## 🔴 The Problem

**Symptom**: Pagination fix works in Postman but not in web app
**Root Cause**: Redis is caching the OLD response before your pagination fix

---

## 🏗️ Cache Architecture

Your API has **3 layers of caching**:

```
┌─────────────────────────────────────────────────────────┐
│                     USER REQUEST                        │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│           LAYER 1: Browser Cache (5 min)                │
│  • Cache-Control: public, max-age=300                   │
│  • ETag headers                                         │
│  ✅ FIX: Hard refresh (Ctrl+Shift+R)                    │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│     LAYER 2: Redis Cache (15-30 min) ◄── STUCK HERE!   │
│  ┌───────────────────────────────────────────────┐     │
│  │  Key: models:catalog:full                     │     │
│  │  TTL: 15 minutes (900 seconds)                │     │
│  │  Data: { total: 350, returned: 50, ... }     │ ❌  │
│  └───────────────────────────────────────────────┘     │
│  ┌───────────────────────────────────────────────┐     │
│  │  Key: models:provider:openrouter              │     │
│  │  TTL: 30 minutes (1800 seconds)               │     │
│  │  Data: [... 350 models ...]                   │ ❌  │
│  └───────────────────────────────────────────────┘     │
│  ✅ FIX: POST /cache/clear                              │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│      LAYER 3: In-Memory Cache (1 hour)                  │
│  • Python dictionaries                                  │
│  • _models_cache, _providers_cache, etc.               │
│  ✅ FIX: POST /cache/clear (also clears this)           │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│           LAYER 4: External Providers                   │
│  • OpenRouter API                                       │
│  • Groq API                                            │
│  • Fireworks API                                       │
│  • ... (30+ providers)                                 │
└─────────────────────────────────────────────────────────┘
```

---

## 🎯 What is Redis?

**Redis** = **RE**mote **DI**ctionary **S**erver

Think of it as a **super-fast shared memory** for your API:

| Feature | Redis | Database | Memory |
|---------|-------|----------|--------|
| Speed | ⚡ 50ms | 🐢 500ms | 🚀 1ms |
| Shared | ✅ Yes | ✅ Yes | ❌ No |
| Persistent | ⚠️ Temporary | ✅ Yes | ❌ No |
| Use Case | Cache | Storage | Variables |

### **Redis Cache Keys in Your System**

```bash
# Full catalog (all providers merged)
models:catalog:full                 # TTL: 15 min

# Individual provider catalogs
models:provider:openrouter          # TTL: 30 min
models:provider:groq                # TTL: 30 min
models:provider:fireworks           # TTL: 30 min
models:provider:together            # TTL: 30 min
models:provider:deepinfra           # TTL: 30 min
models:provider:featherless         # TTL: 30 min
models:provider:chutes              # TTL: 30 min
models:provider:cerebras            # TTL: 30 min
models:provider:xai                 # TTL: 30 min
models:provider:novita              # TTL: 30 min
models:provider:hug                 # TTL: 30 min
... (30+ more)

# Individual model metadata
models:model:openai/gpt-4           # TTL: 60 min
models:model:anthropic/claude-3     # TTL: 60 min
... (thousands of entries)

# Pricing data
models:pricing:openai/gpt-4         # TTL: 60 min
... (thousands of entries)
```

---

## 🔍 Why Postman Works But Web App Doesn't

### **Theory 1: Cache Headers**

**Postman**:
- Doesn't cache responses by default
- Each request is fresh
- Hits the API directly

**Web Browser**:
- Respects `Cache-Control` headers (5 min cache)
- May have service workers caching data
- May have old data in memory

### **Theory 2: Different Request Parameters**

Check if Postman and web app are calling different URLs:

**Postman**: `GET /models?gateway=all&limit=100`
**Web App**: `GET /models` (missing gateway parameter?)

### **Theory 3: Redis Cache Hit/Miss**

```
Request #1 (Postman):
  → Redis MISS (cache expired or not yet cached)
  → Fetch from API with NEW pagination code ✅
  → Cache in Redis for 15 min
  → Return 100 models ✅

Request #2 (Web App):
  → Redis HIT (cache still valid from before fix)
  → Return OLD cached response with 50 models ❌
  → Doesn't execute NEW pagination code
```

---

## 🧹 How to Clear Redis Cache

### **Method 1: API Endpoint (Recommended)**

```bash
# Clear ALL caches (Redis + Memory)
curl -X POST "https://api.gatewayz.ai/cache/clear"

# Response:
{
    "success": true,
    "message": "All caches cleared",
    "gateways_cleared": ["openrouter", "groq", ..., "providers"],
    "timestamp": "2025-01-27T..."
}
```

### **Method 2: Invalidate Specific Cache**

```bash
# Clear only models cache
curl -X POST "https://api.gatewayz.ai/api/cache/invalidate?cache_type=models"

# Clear only providers cache
curl -X POST "https://api.gatewayz.ai/api/cache/invalidate?cache_type=providers"

# Clear specific gateway
curl -X POST "https://api.gatewayz.ai/cache/clear?gateway=openrouter"
```

### **Method 3: Refresh (Clear + Reload)**

```bash
# Clear cache AND fetch fresh data immediately
curl -X POST "https://api.gatewayz.ai/cache/refresh?gateway=all"

# Response:
{
    "success": true,
    "gateway": "all",
    "models_count": 12543,
    "cached_at": "2025-01-27T...",
    "message": "Cache refreshed successfully for all"
}
```

### **Method 4: Direct Redis Access**

If you have access to Redis server:

```bash
# Connect to Redis
redis-cli -h your-redis-host -p 6379

# List all model cache keys
KEYS models:*

# Delete full catalog cache
DEL models:catalog:full

# Delete all provider caches
DEL models:provider:*

# Delete EVERYTHING (nuclear option)
FLUSHDB
```

### **Method 5: Wait for TTL**

Redis will automatically expire old cache:
- **Full catalog**: Expires after 15 minutes
- **Provider catalogs**: Expire after 30 minutes
- **Model metadata**: Expires after 60 minutes

---

## 🚀 Complete Fix Workflow

### **Step 1: Clear ALL Caches**

```bash
curl -X POST "https://api.gatewayz.ai/cache/clear"
```

### **Step 2: Invalidate Cache**

```bash
curl -X POST "https://api.gatewayz.ai/api/cache/invalidate"
```

### **Step 3: Refresh with Fresh Data**

```bash
curl -X POST "https://api.gatewayz.ai/cache/refresh?gateway=all"
```

### **Step 4: Clear Browser Cache**

**Chrome/Edge**:
1. Press `F12` (DevTools)
2. Right-click Refresh button
3. Select "Empty Cache and Hard Reload"

**Firefox**:
1. Press `F12` (DevTools)
2. Network tab → Check "Disable cache"
3. Press `Ctrl+Shift+R` (hard refresh)

**Safari**:
1. Safari → Settings → Advanced
2. Enable "Show Develop menu"
3. Develop → Empty Caches
4. Refresh page

### **Step 5: Test**

```bash
curl "https://api.gatewayz.ai/models?gateway=all&limit=100" | jq '{total, returned, has_more}'

# Expected:
{
  "total": 12543,
  "returned": 100,
  "has_more": true
}
```

---

## 🔬 Debugging Redis Cache

### **Check if Redis is Working**

```bash
GET /cache/status?gateway=all
```

Response:
```json
{
    "gateway": "all",
    "is_cached": true,
    "models_count": 12543,
    "cached_at": "2025-01-27T10:30:00Z",
    "age_seconds": 120,
    "ttl_seconds": 780
}
```

### **Check Cache Statistics**

```bash
GET /cache/stats
```

Response:
```json
{
    "hits": 1523,
    "misses": 42,
    "sets": 45,
    "errors": 0,
    "invalidations": 3,
    "hit_rate_percent": 97.32,
    "total_requests": 1565,
    "redis_available": true,
    "full_catalog_cached": true,
    "provider_catalogs_count": 28,
    "models_cached_count": 0,
    "pricing_cached_count": 0
}
```

### **Monitor Cache in Real-Time**

```bash
# Watch Redis commands in real-time
redis-cli MONITOR

# Example output:
1627384756.123 "GET" "models:catalog:full"
1627384756.124 "SETEX" "models:catalog:full" "900" "{...}"
1627384756.125 "GET" "models:provider:openrouter"
```

---

## 🎯 Best Practices

### **1. Clear Cache After Deployment**

After deploying code changes that affect model responses:

```bash
#!/bin/bash
# deploy.sh

# Deploy code
git pull
# ... deployment steps ...

# Clear caches
curl -X POST "https://api.gatewayz.ai/cache/clear"
curl -X POST "https://api.gatewayz.ai/cache/refresh?gateway=all"

echo "✅ Deployment complete and caches refreshed!"
```

### **2. Use Short TTLs for Frequently Changing Data**

```python
# For data that changes often
TTL_FULL_CATALOG = 300  # 5 minutes

# For relatively static data
TTL_MODEL_METADATA = 3600  # 60 minutes
```

### **3. Implement Cache Versioning**

```python
# Add version to cache keys
PREFIX_FULL_CATALOG = f"models:catalog:full:v{APP_VERSION}"
```

When you deploy a new version, old cache keys are automatically ignored!

### **4. Use Background Refresh**

```python
# Refresh cache in background before it expires
if time_until_expiry < 60:  # Less than 1 minute left
    asyncio.create_task(refresh_cache())
```

### **5. Monitor Cache Hit Rate**

Aim for **>95% hit rate** for optimal performance:

```
Hit Rate = hits / (hits + misses) * 100

Good: >95%
Ok: 80-95%
Bad: <80%
```

---

## 📊 Performance Impact

### **Without Redis Cache**

```
Request → Fetch from 30+ providers → Aggregate → Process → Return
Time: 5-10 seconds per request ⏱️
```

### **With Redis Cache**

```
Request → Check Redis → Return cached data
Time: 50ms per request ⚡
```

**Performance Improvement**: **100-200x faster!** 🚀

---

## 🐛 Common Issues

### **Issue 1: Cache Not Clearing**

**Symptoms**: After clearing cache, still seeing old data

**Causes**:
- Browser cache not cleared
- Multiple API instances with separate Redis connections
- Redis not actually connected

**Fix**:
```bash
# 1. Clear server cache
curl -X POST "https://api.gatewayz.ai/cache/clear"

# 2. Clear browser cache (hard refresh)
Ctrl+Shift+R or Cmd+Shift+R

# 3. Verify Redis is connected
curl "https://api.gatewayz.ai/cache/stats" | jq '.redis_available'
```

### **Issue 2: Inconsistent Data Between Requests**

**Symptoms**: Different responses for same request

**Causes**:
- Multiple API instances with inconsistent caches
- Cache being updated while you're reading

**Fix**:
Use distributed cache invalidation or Redis pub/sub for cache synchronization.

### **Issue 3: Cache Never Expires**

**Symptoms**: Data never updates even after TTL passes

**Causes**:
- TTL set to -1 (never expire)
- Cache being constantly refreshed

**Fix**:
```bash
# Check TTL
redis-cli TTL models:catalog:full

# If -1, delete and recreate with proper TTL
redis-cli DEL models:catalog:full
```

---

## 📚 Related Documentation

- [Pagination Guide](./PAGINATION.md)
- [Cache Configuration](../src/services/model_catalog_cache.py)
- [Redis Config](../src/config/redis_config.py)

---

## 🆘 Quick Help

**"Postman works, web app doesn't"**
→ Clear Redis cache: `POST /cache/clear`

**"Data is old/stale"**
→ Refresh cache: `POST /cache/refresh?gateway=all`

**"Changes not showing up"**
→ Hard refresh browser: `Ctrl+Shift+R`

**"How long until cache expires?"**
→ Check status: `GET /cache/status?gateway=all`

---

**Last Updated**: 2025-01-27
