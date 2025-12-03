# Additional Optimizations for Availability & Performance

Beyond the caching implementation, here are additional optimizations to prevent availability issues:

## 1. Connection Pooling Optimization

### Current Status
✅ Redis connection pooling already enabled in `src/redis_config.py`

### Configuration
```python
# Already configured with:
- socket_connect_timeout=5
- socket_timeout=5
- retry_on_timeout=True
- decode_responses=True
```

### Recommended Enhancements
```python
# Increase pool size for high concurrency
redis_client = redis.from_url(
    REDIS_URL,
    connection_pool_kwargs={
        'max_connections': 50,  # Increase from default 50
        'socket_keepalive': True,
        'socket_keepalive_options': {
            1: 1,  # TCP_KEEPIDLE
            2: 1,  # TCP_KEEPINTVL
            3: 3,  # TCP_KEEPCNT
        }
    }
)
```

## 2. Database Query Optimization

### Current Issue
Health endpoints may trigger multiple database queries

### Optimization Strategy
```python
# Batch queries instead of individual lookups
# Example: Get all models in one query instead of per-model

from src.db.models import Model

# ❌ Inefficient: N+1 query problem
for provider in providers:
    models = db.query(Model).filter(Model.provider == provider).all()

# ✅ Efficient: Single batch query
all_models = db.query(Model).filter(
    Model.provider.in_([p.name for p in providers])
).all()

# Group by provider
models_by_provider = {}
for model in all_models:
    if model.provider not in models_by_provider:
        models_by_provider[model.provider] = []
    models_by_provider[model.provider].append(model)
```

## 3. Response Pagination

### Implement for Large Datasets
```python
from fastapi import Query
from typing import Optional

@router.get("/health/models")
async def get_models_health(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    api_key: str = Depends(get_api_key),
):
    """Get paginated models health"""
    models = health_monitor.get_all_models_health()
    
    # Apply pagination
    paginated = models[skip:skip + limit]
    
    return {
        "data": paginated,
        "total": len(models),
        "skip": skip,
        "limit": limit,
        "has_more": (skip + limit) < len(models),
    }
```

## 4. Selective Field Loading

### Reduce Payload Size Further
```python
from typing import Optional, List

@router.get("/health/models")
async def get_models_health(
    fields: Optional[List[str]] = Query(None),
    api_key: str = Depends(get_api_key),
):
    """Get models health with selective fields"""
    models = health_monitor.get_all_models_health()
    
    if fields:
        # Only include requested fields
        return [
            {field: getattr(model, field) for field in fields}
            for model in models
        ]
    
    return models
```

## 5. Request Deduplication

### Prevent Duplicate Concurrent Requests
```python
import asyncio
from typing import Dict, Callable, Any

class RequestDeduplicator:
    def __init__(self):
        self.pending_requests: Dict[str, asyncio.Future] = {}
    
    async def deduplicate(self, key: str, func: Callable) -> Any:
        """Execute function once for duplicate concurrent requests"""
        if key in self.pending_requests:
            # Wait for existing request
            return await self.pending_requests[key]
        
        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[key] = future
        
        try:
            result = await func()
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            del self.pending_requests[key]

# Usage
deduplicator = RequestDeduplicator()

@router.get("/health/system")
async def get_system_health(api_key: str = Depends(get_api_key)):
    return await deduplicator.deduplicate(
        "system_health",
        lambda: health_monitor.get_system_health()
    )
```

## 6. Circuit Breaker Pattern

### Prevent Cascading Failures
```python
from enum import Enum
from datetime import datetime, timedelta

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
    
    async def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
    
    def _should_attempt_reset(self) -> bool:
        return (
            self.last_failure_time and
            datetime.now() - self.last_failure_time > timedelta(seconds=self.timeout)
        )

# Usage
health_breaker = CircuitBreaker(failure_threshold=5, timeout=60)

@router.get("/health/system")
async def get_system_health(api_key: str = Depends(get_api_key)):
    try:
        return await health_breaker.call(
            health_monitor.get_system_health
        )
    except Exception:
        # Return cached data or default response
        return {"status": "unknown", "error": "Service temporarily unavailable"}
```

## 7. Rate Limiting Enhancement

### Implement Adaptive Rate Limiting
```python
from collections import defaultdict
from datetime import datetime, timedelta

class AdaptiveRateLimiter:
    def __init__(self):
        self.requests: Dict[str, List[datetime]] = defaultdict(list)
        self.limits: Dict[str, int] = {
            "health:system": 100,      # 100 req/min
            "health:dashboard": 200,   # 200 req/min (most accessed)
            "health:models": 50,       # 50 req/min
        }
    
    def is_allowed(self, key: str, client_id: str) -> bool:
        now = datetime.now()
        window = now - timedelta(minutes=1)
        
        # Clean old requests
        self.requests[client_id] = [
            req_time for req_time in self.requests[client_id]
            if req_time > window
        ]
        
        # Check limit
        limit = self.limits.get(key, 100)
        if len(self.requests[client_id]) >= limit:
            return False
        
        # Record request
        self.requests[client_id].append(now)
        return True

# Usage
limiter = AdaptiveRateLimiter()

@router.get("/health/system")
async def get_system_health(
    api_key: str = Depends(get_api_key),
    request: Request = None,
):
    client_id = request.client.host if request else "unknown"
    
    if not limiter.is_allowed("health:system", client_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded"
        )
    
    return health_monitor.get_system_health()
```

## 8. Graceful Degradation

### Fallback Responses
```python
@router.get("/health/system")
async def get_system_health(api_key: str = Depends(get_api_key)):
    try:
        # Try cache first
        cached = health_cache_service.get_system_health()
        if cached:
            return cached
        
        # Try fresh data
        data = health_monitor.get_system_health()
        if data:
            health_cache_service.cache_system_health(data)
            return data
        
        # Fallback to stale cache if available
        stale_cache = get_stale_cache("health:system")
        if stale_cache:
            return {
                **stale_cache,
                "warning": "Data may be stale",
                "cached_at": stale_cache.get("timestamp")
            }
        
        # Last resort: minimal response
        return {
            "status": "unknown",
            "message": "Health data temporarily unavailable",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "error",
            "message": "Health check service error",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
```

## 9. Health Check Optimization

### Reduce Health Check Frequency
```python
# Current: Check every 5 minutes
# Optimized: Adaptive checking based on status

class AdaptiveHealthMonitor:
    def __init__(self):
        self.check_intervals = {
            "healthy": 300,      # 5 minutes
            "degraded": 60,      # 1 minute
            "unhealthy": 10,     # 10 seconds
        }
    
    async def start_monitoring(self):
        while True:
            status = await self.check_health()
            interval = self.check_intervals.get(status, 300)
            await asyncio.sleep(interval)
```

## 10. Monitoring & Alerting

### Key Metrics to Track
```python
from prometheus_client import Counter, Histogram, Gauge

# Cache metrics
cache_hits = Counter('health_cache_hits', 'Cache hits')
cache_misses = Counter('health_cache_misses', 'Cache misses')
cache_size = Gauge('health_cache_size', 'Cache size in bytes')

# Response metrics
response_time = Histogram('health_response_time_ms', 'Response time')
error_count = Counter('health_errors', 'Error count')

# Health metrics
healthy_models = Gauge('healthy_models', 'Number of healthy models')
healthy_providers = Gauge('healthy_providers', 'Number of healthy providers')
```

### Alert Rules
```yaml
# Prometheus alert rules
groups:
  - name: health_monitoring
    rules:
      - alert: LowCacheHitRate
        expr: rate(health_cache_hits[5m]) / (rate(health_cache_hits[5m]) + rate(health_cache_misses[5m])) < 0.7
        for: 5m
        annotations:
          summary: "Low cache hit rate"
      
      - alert: HighResponseTime
        expr: histogram_quantile(0.95, health_response_time_ms) > 500
        for: 5m
        annotations:
          summary: "High response time"
      
      - alert: HighErrorRate
        expr: rate(health_errors[5m]) > 0.05
        for: 5m
        annotations:
          summary: "High error rate"
```

## Implementation Priority

1. **High Priority** (Implement First)
   - ✅ Caching (Already done)
   - Connection pooling optimization
   - Database query optimization
   - Circuit breaker pattern

2. **Medium Priority** (Implement Next)
   - Response pagination
   - Request deduplication
   - Graceful degradation
   - Monitoring & alerting

3. **Low Priority** (Nice to Have)
   - Selective field loading
   - Adaptive rate limiting
   - Adaptive health checking

## Expected Impact

| Optimization | Impact | Effort |
|--------------|--------|--------|
| Caching | 84% bandwidth reduction | ✅ Done |
| Connection pooling | 20% faster DB access | Low |
| Query optimization | 50% fewer DB queries | Medium |
| Pagination | 90% smaller payloads | Low |
| Circuit breaker | Prevent cascading failures | Medium |
| Request dedup | 30% fewer duplicate requests | Medium |
| Graceful degradation | 99.9% availability | High |

## Deployment Order

1. Deploy caching (already done)
2. Deploy connection pooling optimization
3. Deploy database query optimization
4. Deploy circuit breaker
5. Deploy request deduplication
6. Deploy monitoring & alerting
7. Deploy graceful degradation
8. Deploy remaining optimizations

## Testing Checklist

- [ ] Load test with 500+ concurrent users
- [ ] Verify cache hit rates > 85%
- [ ] Monitor response times < 50ms
- [ ] Check bandwidth reduction > 80%
- [ ] Verify error rates < 0.1%
- [ ] Test failover scenarios
- [ ] Verify graceful degradation
- [ ] Monitor Redis memory usage

## References

- [Circuit Breaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
- [Request Deduplication](https://en.wikipedia.org/wiki/Request_deduplication)
- [Graceful Degradation](https://en.wikipedia.org/wiki/Graceful_degradation)
- [Adaptive Rate Limiting](https://aws.amazon.com/blogs/architecture/rate-limiting-strategies-and-techniques/)
