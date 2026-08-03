# Metrics Volatility Issue: Random Values on Page Refresh

**Issue:** System health scores change dramatically on page refresh (e.g., 10.5 → 102)

**Root Cause:** Health metrics are recalculated on every request with non-deterministic deltas instead of being cached/aggregated.

**Severity:** HIGH - Breaks dashboards and makes monitoring unreliable

**Date Identified:** 2025-12-30

---

## Root Cause Analysis

### The Problem Code

**File:** `src/services/redis_metrics.py` (lines 167-186)

```python
async def _update_health_score_pipe(self, pipe, provider: str, success: bool):
    """Update provider health score (internal helper for pipeline)"""

    # Adjust health score based on success/failure
    delta = 2 if success else -5  # ← PROBLEM: Fixed deltas per request!

    try:
        current = self.redis.zscore("provider_health", provider)
        if current is None:
            current = 100.0

        new_score = max(0.0, min(100.0, current + delta))  # ← Volatile!
        pipe.zadd("provider_health", {provider: new_score})
    except Exception:
        # If we can't get current score, just set to a random default
        pipe.zadd("provider_health", {provider: 85.0 if success else 50.0})
```

### Why This Creates Volatility

**Example Scenario:**

1. **Initial state:** Provider health = 10.5
2. **First refresh** - 5 successful requests + 1 failure:
   - Change: `(5 × +2) + (1 × -5) = +5`
   - New score: `10.5 + 5 = 15.5`
3. **Second refresh** - Different request pattern (10 successes, 0 failures):
   - Change: `(10 × +2) + (0 × -5) = +20`
   - New score: `15.5 + 20 = 35.5`
4. **Third refresh** - One provider failure (0 successes, 1 failure):
   - Change: `(0 × +2) + (1 × -5) = -5`
   - New score: `35.5 - 5 = 30.5`

**Result:** Score bounces from 10.5 → 15.5 → 35.5 → 30.5 on each refresh

### Impact on Dashboards

```
Grafana Dashboard displays:
┌─────────────────────────────────┐
│ System Health Score: 10.5       │  ← First load
│ [Refresh...]                    │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│ System Health Score: 102        │  ← Second load (same query!)
│ [Refresh...]                    │     Different request pattern
└─────────────────────────────────┘
```

**User Impact:**
- ❌ Can't trust metric values
- ❌ Can't set reliable alerts
- ❌ Can't detect actual issues (too much noise)
- ❌ Dashboard looks broken/unreliable

---

## Why This Happens

### 1. Per-Request Metric Updates

Every single request modifies health scores:

```python
# In src/routes/chat.py, messages.py, etc.
async def make_request(provider, model, ...):
    # ... make request ...
    success = response.status == 200

    # This updates health score in Redis:
    await redis_metrics.record_request(
        provider=provider,
        model=model,
        success=success,  # ← Triggers delta update
        ...
    )
```

**Problem:** Each request changes the score, so refreshing with different traffic patterns gives different scores.

### 2. No Caching or Aggregation

Health scores are read directly from Redis on each request:

```python
# In src/routes/monitoring.py line 256
health_scores = await redis_metrics.get_all_provider_health()  # ← Fresh read, no cache
```

**Problem:** No TTL on the displayed score, so volatile underlying data produces volatile display.

### 3. Fixed Delta Logic (Not Statistical)

The `+2` and `-5` deltas are arbitrary:

```python
delta = 2 if success else -5  # ← Why these numbers?
```

**Problem:** Doesn't reflect actual provider health. A single failure shouldn't drop score by 5 points.

---

## Solutions

### Solution 1: Cache Health Scores (Quick Fix - 30 minutes)

**Implementation:** Cache calculated health scores for 5-15 minutes instead of reading raw Redis data on every request.

**Files to modify:**
- `src/services/redis_metrics.py` - Add caching layer
- `src/routes/monitoring.py` - Use cached values

**Code changes:**

```python
# In src/services/redis_metrics.py

class RedisMetrics:
    def __init__(self):
        self.health_cache = {}
        self.cache_ttl = 300  # 5 minutes
        self.cache_timestamp = {}

    async def get_all_provider_health(self) -> dict[str, float]:
        """Get all provider health scores with caching"""
        now = time.time()

        # Return cached if still valid
        if hasattr(self, '_cached_health'):
            if now - self._cache_time < self.cache_ttl:
                return self._cached_health

        # Fetch fresh scores
        scores = {}
        for provider in self.get_all_providers():
            score = self.redis.zscore("provider_health", provider)
            scores[provider] = float(score) if score else 100.0

        # Cache for next 5 minutes
        self._cached_health = scores
        self._cache_time = now

        return scores
```

**Pros:**
- ✅ Quick to implement (1-2 hours)
- ✅ Immediate fix for dashboard volatility
- ✅ Minimal code changes

**Cons:**
- ❌ Doesn't fix underlying calculation logic
- ❌ Up to 5 minutes of stale data
- ❌ Still uses wrong delta method

---

### Solution 2: Use Sliding Window Averages (Better - 2-3 hours)

**Implementation:** Calculate health as a sliding window average of success rate rather than per-request deltas.

**Formula:**
```
Health Score = (Success Rate % × 100) × Weight + Latency Score × Weight
  where:
    - Success Rate = successful_requests / total_requests (last hour)
    - Latency Score = (1 - min(latency_p95 / acceptable_latency, 1.0)) × 100
```

**Example:**
```
OpenRouter in last hour:
- 980 successful / 1000 total = 98% success rate
- P95 latency = 250ms (acceptable = 500ms)
- Score = (98 × 0.7) + ((1 - 250/500) × 100 × 0.3)
        = 68.6 + 15.0
        = 83.6
```

**Code Changes:**

```python
# In src/services/redis_metrics.py

async def calculate_health_score(self, provider: str, window_minutes: int = 60) -> float:
    """
    Calculate health score based on sliding window statistics.

    Args:
        provider: Provider name
        window_minutes: Time window for aggregation (default 1 hour)

    Returns:
        Health score 0-100
    """
    # Get hourly metrics for last N hours
    scores = []
    for hours_ago in range(window_minutes // 60 + 1):
        metrics = self.get_hourly_metrics(provider, hours_ago)

        if metrics['total_requests'] == 0:
            continue

        # Calculate success rate
        success_rate = (
            metrics['successful_requests'] / metrics['total_requests']
        ) * 100

        # Calculate latency score
        latency_p95 = metrics.get('latency_p95_ms', 1000)
        acceptable_latency = 500  # ms
        latency_score = max(0, (1 - latency_p95 / acceptable_latency) * 100)

        # Weighted combination
        score = (success_rate * 0.7) + (latency_score * 0.3)
        scores.append(score)

    # Return average of all windows
    return sum(scores) / len(scores) if scores else 100.0
```

**Pros:**
- ✅ Statistically sound (based on real data)
- ✅ Reflects actual provider health
- ✅ Stable across refreshes
- ✅ Can set realistic alerts

**Cons:**
- ❌ Requires more code changes
- ❌ Requires hourly metrics aggregation

---

### Solution 3: Disable Real-Time Updates, Use Periodic Aggregation (Best - 4-5 hours)

**Implementation:** Stop updating health on every request. Instead, calculate scores once per minute from aggregated metrics.

**Architecture:**
```
Request Flow:
  Request → Record to Redis metrics → Success

Background Job (every 1 minute):
  1. Fetch all metrics from Redis
  2. Aggregate by provider
  3. Calculate health scores
  4. Store in separate "health_scores_current" key
  5. Cache for next 60 seconds

Display Flow:
  Dashboard → GET /api/monitoring/health → Get cached scores (stable!)
```

**Code Changes:**

```python
# In src/services/redis_metrics.py

async def start_health_calculation_job(self):
    """Start background job to calculate health scores periodically"""
    while True:
        try:
            await asyncio.sleep(60)  # Recalculate every minute
            await self._recalculate_all_health_scores()
        except Exception as e:
            logger.error(f"Health calculation error: {e}")

async def _recalculate_all_health_scores(self):
    """Recalculate all provider health scores from aggregated metrics"""
    now = datetime.now(timezone.utc)

    # Get current hour's metrics
    hour_key = now.strftime("%Y-%m-%d:%H")

    health_scores = {}
    for provider in self.get_all_providers():
        metrics_key = f"metrics:{provider}:{hour_key}"
        metrics = self.redis.hgetall(metrics_key)

        if not metrics:
            health_scores[provider] = 100.0
            continue

        total = int(metrics.get(b'total_requests', 0))
        successful = int(metrics.get(b'successful_requests', 0))

        if total == 0:
            success_rate = 100.0
        else:
            success_rate = (successful / total) * 100

        # Map success rate to health (0-100)
        health = success_rate  # Or more sophisticated calculation
        health_scores[provider] = health

    # Store calculated scores in a stable location
    self.redis.hset(
        "provider_health_scores_current",
        mapping={k: v for k, v in health_scores.items()}
    )
    self.redis.expire("provider_health_scores_current", 120)  # 2 min TTL

async def get_all_provider_health(self) -> dict[str, float]:
    """Get cached health scores (stable across page refreshes)"""
    # Try to get pre-calculated scores
    cached = self.redis.hgetall("provider_health_scores_current")
    if cached:
        return {k.decode(): float(v) for k, v in cached.items()}

    # Fallback to real-time calculation if cache empty
    return await self._calculate_all_health_scores()
```

**Pros:**
- ✅ Most stable metrics (updated every minute, not per-request)
- ✅ Best for dashboard reliability
- ✅ Aligns with observability best practices
- ✅ Easy to add per-minute alerting

**Cons:**
- ❌ Metrics are 1 minute old (slight staleness)
- ❌ Requires background job management
- ❌ More complex implementation

---

## Recommended Solution: Hybrid Approach

**Combine Solutions 1 + 3** (Time: 2-3 hours, Best balance):

1. **Immediate:** Add 5-minute caching to health scores (Solution 1)
2. **Medium-term:** Add background aggregation job (Solution 3)
3. **Long-term:** Implement sliding window calculations (Solution 2)

### Phase 1: Immediate Fix (Today - 30 mins)

```python
# src/services/redis_metrics.py - Add simple caching

class RedisMetrics:
    def __init__(self):
        self._health_cache = None
        self._health_cache_time = 0

    async def get_all_provider_health(self) -> dict[str, float]:
        """Get provider health with 5-minute caching"""
        now = time.time()

        # Return cache if less than 5 minutes old
        if (self._health_cache is not None and
            now - self._health_cache_time < 300):
            return self._health_cache

        # Calculate fresh scores
        scores = {}
        for provider in self.get_all_providers():
            score = self.redis.zscore("provider_health", provider)
            scores[provider] = float(score) if score else 100.0

        # Update cache
        self._health_cache = scores
        self._health_cache_time = now

        return scores
```

### Phase 2: Better Calculation (This week - 2 hours)

```python
# src/services/redis_metrics.py - Use actual metrics

async def _recalculate_health_scores(self):
    """Calculate health from actual request metrics"""
    # Implementation of Solution 3 above
```

### Phase 3: Proper Implementation (Next sprint - 4 hours)

```python
# Add sliding window averages and background job
```

---

## Verification

### Before Fix
```bash
$ curl http://localhost:8000/api/monitoring/health
[
  {"provider": "openrouter", "health_score": 10.5},
  ...
]

# Refresh immediately
$ curl http://localhost:8000/api/monitoring/health
[
  {"provider": "openrouter", "health_score": 102},  # ← Different!
  ...
]
```

### After Fix
```bash
$ curl http://localhost:8000/api/monitoring/health
[
  {"provider": "openrouter", "health_score": 87.3},
  ...
]

# Refresh immediately (within 5 minutes)
$ curl http://localhost:8000/api/monitoring/health
[
  {"provider": "openrouter", "health_score": 87.3},  # ← Same! (cached)
  ...
]

# After 5 minutes, values might change slightly based on new traffic
# But changes will be gradual, not dramatic jumps
```

---

## Affected Endpoints

These endpoints are returning volatile metrics:

1. `GET /api/monitoring/health` - Provider health scores
2. `GET /api/monitoring/health/{provider}` - Specific provider health
3. `GET /api/monitoring/stats/realtime` - Real-time statistics with health
4. Grafana dashboards showing health metrics

---

## Priority & Timeline

| Phase | Fix | Time | Impact |
|-------|-----|------|--------|
| **P0 (Now)** | Add 5-min cache | 30 min | Stops dashboard volatility |
| **P1 (Today)** | Fix health calculation | 2 hours | More accurate scores |
| **P2 (Week)** | Background aggregation | 4 hours | Production-ready |

---

## Files to Modify

```
src/services/redis_metrics.py        # Health score calculation
src/routes/monitoring.py             # Cache layer
src/services/prometheus_metrics.py   # Export cached values
src/routes/grafana_metrics.py        # Use cached scores
```

---

## Testing

### Unit Test
```python
def test_health_cache_stability():
    """Verify health scores don't change within cache window"""
    redis_metrics = RedisMetrics()

    # Get score
    score1 = await redis_metrics.get_all_provider_health()

    # Make multiple requests
    for _ in range(100):
        await redis_metrics.record_request(
            provider="openrouter",
            model="gpt-4",
            success=True,
            latency_ms=100,
            cost=0.1,
            tokens_input=100,
            tokens_output=100
        )

    # Score should be same (cached)
    score2 = await redis_metrics.get_all_provider_health()
    assert score1 == score2, f"Scores changed: {score1} != {score2}"

    # After cache expires
    await asyncio.sleep(301)
    score3 = await redis_metrics.get_all_provider_health()
    # Score may be different, but should be reasonable
    assert 0 <= score3["openrouter"] <= 100
```

### Integration Test
```python
async def test_dashboard_metric_stability():
    """Verify Grafana dashboard gets consistent values"""
    client = AsyncClient(app=app, base_url="http://test")

    # Get initial health
    resp1 = await client.get("/api/monitoring/health")
    health1 = resp1.json()

    # Simulate user clicking refresh
    await asyncio.sleep(0.5)
    resp2 = await client.get("/api/monitoring/health")
    health2 = resp2.json()

    # Values should be identical within cache window
    for h1, h2 in zip(health1, health2):
        assert h1["health_score"] == h2["health_score"]
```

---

## Summary

**Root Cause:** Health scores are recalculated per-request with fixed deltas (+2/-5), not aggregated metrics.

**Immediate Impact:** Dashboard shows different values on refresh (10.5 → 102), breaking user confidence.

**Quick Fix:** Cache health scores for 5 minutes (30 minutes implementation).

**Proper Fix:** Calculate health from aggregated metrics once per minute (4 hours implementation).

**Next Steps:**
1. ✅ Implement 5-minute cache (P0 - today)
2. ✅ Fix health calculation formula (P1 - today)
3. ✅ Add background aggregation job (P2 - this week)

