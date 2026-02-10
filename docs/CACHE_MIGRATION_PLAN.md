# Cache Migration Plan: cache.py → model_catalog_cache.py

**Status:** Planning
**Created:** 2026-02-08
**Target Completion:** TBD
**Priority:** Medium (50+ files affected)

---

## Executive Summary

Migrate from deprecated in-memory `cache.py` to Redis-based `model_catalog_cache.py` system across 50+ files.

**Why?**
- ✅ Distributed caching (shared across instances)
- ✅ Persistent across restarts
- ✅ Better observability (metrics, monitoring)
- ✅ Automatic invalidation & cache warming
- ✅ Unified API & error handling

---

## Current State Analysis

### Usage Patterns Identified

**1. Direct Cache Dictionary Access (30+ providers)**
```python
from src.cache import _xxx_models_cache

# Pattern: Direct dictionary access
if _xxx_models_cache["data"] is not None:
    return _xxx_models_cache["data"]
```

**2. Error State Functions (10+ files)**
```python
from src.cache import clear_gateway_error, set_gateway_error, is_gateway_in_error_state

# Pattern: Error tracking with exponential backoff
if is_gateway_in_error_state("provider"):
    return cached_or_default
set_gateway_error("provider", error_message)
clear_gateway_error("provider")  # On success
```

**3. Cache Management Functions (3 files)**
```python
from src.cache import clear_models_cache, clear_modelz_cache, get_modelz_cache

# Pattern: Explicit cache invalidation
clear_models_cache("openrouter")
```

**4. Cache Initialization (2 files)**
```python
from src.cache import initialize_fal_cache_from_catalog, initialize_featherless_cache_from_catalog

# Pattern: Startup cache warming
initialize_fal_cache_from_catalog()
```

**5. Legacy Cache Access (2 files)**
```python
from src.cache import _models_cache, _provider_cache, _huggingface_cache

# Pattern: Admin/system endpoints accessing cache metadata
```

---

## Migration Strategy

### Phase 1: Add Compatibility Layer (Week 1)
**Goal:** Make cache.py delegate to model_catalog_cache.py without breaking existing code

**Steps:**
1. Add wrapper functions in `cache.py` that delegate to new cache
2. Log deprecation warnings for monitoring
3. Test in staging environment
4. Deploy to production (no code changes needed)

**Implementation:**
```python
# In cache.py - Add compatibility layer
def _get_cache_via_redis(provider_slug: str):
    """COMPATIBILITY: Delegate to Redis cache"""
    from src.services.model_catalog_cache import get_cached_gateway_catalog
    warnings.warn(f"Using legacy cache.py for {provider_slug}", DeprecationWarning)
    return get_cached_gateway_catalog(provider_slug)

# Update existing cache dictionaries to use Redis behind the scenes
class _CacheDict(dict):
    def __init__(self, provider_slug):
        self.provider_slug = provider_slug
        super().__init__({"data": None, "timestamp": None, "ttl": 3600, "stale_ttl": 7200})

    def __getitem__(self, key):
        if key == "data":
            return _get_cache_via_redis(self.provider_slug)
        return super().__getitem__(key)
```

### Phase 2: Migrate Provider Clients (Week 2-3)
**Goal:** Update all 30+ provider clients to use new cache API

**Priority Order:**
1. High-traffic providers (OpenRouter, DeepInfra, Featherless) - 5 files
2. Medium-traffic providers (Fireworks, Together, Groq, etc.) - 15 files
3. Low-traffic providers (Anannas, Canopywave, etc.) - 10 files

**Per-File Changes:**
```python
# OLD
from src.cache import _openrouter_models_cache, clear_gateway_error, set_gateway_error

if _openrouter_models_cache["data"] is not None:
    return _openrouter_models_cache["data"]

_openrouter_models_cache["data"] = models
_openrouter_models_cache["timestamp"] = datetime.now(UTC)

# NEW
from src.services.model_catalog_cache import (
    get_cached_gateway_catalog,
    set_cached_gateway_catalog,
    invalidate_provider_catalog
)

cached = get_cached_gateway_catalog("openrouter")
if cached:
    return cached

set_cached_gateway_catalog("openrouter", models)
```

**Error State Migration:**
```python
# OLD
from src.cache import is_gateway_in_error_state, set_gateway_error, clear_gateway_error

if is_gateway_in_error_state("openrouter"):
    return []
set_gateway_error("openrouter", str(error))
clear_gateway_error("openrouter")

# NEW - Error tracking built into Redis cache
from src.services.model_catalog_cache import get_model_catalog_cache

cache = get_model_catalog_cache()
# Error handling is automatic with circuit breaker pattern
# Manual error tracking removed - handled by cache layer
```

### Phase 3: Migrate Core Services (Week 3)
**Goal:** Update routes and core services

**Files:**
- `src/routes/catalog.py` - Catalog endpoints
- `src/routes/admin.py` - Admin cache management
- `src/routes/system.py` - System health/cache endpoints
- `src/services/model_catalog_sync.py` - Model sync invalidation
- `src/services/models.py` - Model aggregation
- `src/services/gateway_health_service.py` - Health checks
- `src/services/startup.py` - Startup initialization

**Changes:**
```python
# catalog.py OLD
from src.cache import clear_models_cache
clear_models_cache(gateway)

# catalog.py NEW
from src.services.model_catalog_cache import invalidate_provider_catalog
invalidate_provider_catalog(gateway)
```

### Phase 4: Update Tests (Week 4)
**Goal:** Fix all test imports and mocks

**Files:** 10+ test files
- Update all cache imports
- Update mock objects to use new cache API
- Verify no breaking changes

### Phase 5: Remove cache.py (Week 5)
**Goal:** Delete deprecated file after full migration

**Checklist:**
- [ ] All imports migrated
- [ ] All tests passing
- [ ] Production monitoring shows no cache.py usage
- [ ] Remove `cache.py`
- [ ] Remove compatibility layer
- [ ] Update documentation

---

## Migration Mapping

### Function Mapping Table

| Old (cache.py) | New (model_catalog_cache.py) | Notes |
|----------------|------------------------------|-------|
| `get_models_cache(gateway)` | `get_cached_gateway_catalog(gateway)` | Returns models directly |
| `clear_models_cache(gateway)` | `invalidate_provider_catalog(gateway)` | More descriptive name |
| `_xxx_models_cache["data"]` | `get_cached_gateway_catalog("xxx")` | Abstracted access |
| `_xxx_models_cache["timestamp"]` | N/A - Handled internally | Auto-managed |
| `is_cache_fresh(cache)` | N/A - Handled internally | Built-in TTL |
| `is_gateway_in_error_state(g)` | N/A - Circuit breaker pattern | Auto-handled |
| `set_gateway_error(g, msg)` | N/A - Circuit breaker pattern | Auto-handled |
| `clear_gateway_error(g)` | N/A - Circuit breaker pattern | Auto-handled |
| `initialize_fal_cache_from_catalog()` | `warm_cache()` | Generic cache warming |
| `get_modelz_cache()` | `get_cached_gateway_catalog("modelz")` | Unified API |

### Import Migration Patterns

```python
# Pattern 1: Simple cache access
# OLD
from src.cache import _openrouter_models_cache
models = _openrouter_models_cache["data"]

# NEW
from src.services.model_catalog_cache import get_cached_gateway_catalog
models = get_cached_gateway_catalog("openrouter")

# Pattern 2: Cache with error handling
# OLD
from src.cache import _xxx_models_cache, is_gateway_in_error_state, set_gateway_error
if is_gateway_in_error_state("xxx"):
    return []
try:
    models = fetch_from_api()
    _xxx_models_cache["data"] = models
    _xxx_models_cache["timestamp"] = datetime.now(UTC)
except Exception as e:
    set_gateway_error("xxx", str(e))

# NEW
from src.services.model_catalog_cache import get_cached_gateway_catalog, set_cached_gateway_catalog
cached = get_cached_gateway_catalog("xxx")
if cached:
    return cached
try:
    models = fetch_from_api()
    set_cached_gateway_catalog("xxx", models)
    return models
except Exception as e:
    # Circuit breaker handles error state automatically
    logger.error(f"Failed to fetch models: {e}")
    return []

# Pattern 3: Cache invalidation
# OLD
from src.cache import clear_models_cache
clear_models_cache("openrouter")

# NEW
from src.services.model_catalog_cache import invalidate_provider_catalog
invalidate_provider_catalog("openrouter")

# Pattern 4: Cache initialization
# OLD
from src.cache import initialize_fal_cache_from_catalog
initialize_fal_cache_from_catalog()

# NEW
from src.services.model_catalog_cache import get_model_catalog_cache
cache = get_model_catalog_cache()
cache.warm_cache(providers=["fal"])
```

---

## Files to Update

### Provider Clients (30 files)
**High Priority (High Traffic):**
1. `src/services/openrouter_client.py` - Primary gateway
2. `src/services/deepinfra_client.py` - High usage
3. `src/services/featherless_client.py` - High usage
4. `src/services/fireworks_client.py` - High usage
5. `src/services/together_client.py` - High usage

**Medium Priority (Medium Traffic):**
6. `src/services/groq_client.py`
7. `src/services/cerebras_client.py`
8. `src/services/xai_client.py`
9. `src/services/google_vertex_client.py`
10. `src/services/anthropic_client.py`
11. `src/services/openai_client.py`
12. `src/services/huggingface_client.py`
13. `src/services/huggingface_models.py`
14. `src/services/nebius_client.py`
15. `src/services/novita_client.py`
16. `src/services/clarifai_client.py`
17. `src/services/chutes_client.py`
18. `src/services/aimo_client.py`
19. `src/services/near_client.py`
20. `src/services/fal_image_client.py`

**Low Priority (Low Traffic):**
21. `src/services/vercel_ai_gateway_client.py`
22. `src/services/helicone_client.py`
23. `src/services/aihubmix_client.py`
24. `src/services/anannas_client.py`
25. `src/services/alibaba_cloud_client.py`
26. `src/services/onerouter_client.py`
27. `src/services/zai_client.py`
28. `src/services/modelz_client.py`
29. `src/services/canopywave_client.py`
30. `src/services/morpheus_client.py`

### Core Services (7 files)
31. `src/services/models.py` - Model aggregation (CRITICAL)
32. `src/services/model_catalog_sync.py` - Model sync (CRITICAL)
33. `src/services/gateway_health_service.py` - Health checks
34. `src/services/startup.py` - Startup initialization

### Routes (3 files)
35. `src/routes/catalog.py` - Catalog endpoints (CRITICAL)
36. `src/routes/admin.py` - Admin cache management
37. `src/routes/system.py` - System health/cache

### Tests (10+ files)
38-50. Various test files in `tests/` directory

---

## Risk Assessment

### High Risk Areas
1. **`src/services/models.py`** - Central model aggregation
   - Risk: Breaking model catalog API
   - Mitigation: Extensive testing, feature flag rollout

2. **`src/routes/catalog.py`** - Public API endpoint
   - Risk: API downtime or errors
   - Mitigation: Canary deployment, rollback plan

3. **Cache invalidation timing**
   - Risk: Stale data served to users
   - Mitigation: Aggressive TTLs during migration, monitoring

### Medium Risk Areas
4. Provider clients - May break provider fetching
5. Admin endpoints - May break cache management tools

### Low Risk Areas
6. Test files - Can be fixed iteratively
7. Low-traffic providers - Limited user impact

---

## Testing Strategy

### Unit Tests
```python
def test_provider_client_migration():
    """Test that provider client works with new cache"""
    from src.services.openrouter_client import fetch_models_from_openrouter

    # Clear cache
    from src.services.model_catalog_cache import invalidate_provider_catalog
    invalidate_provider_catalog("openrouter")

    # Fetch models
    models = fetch_models_from_openrouter()

    # Verify cached
    from src.services.model_catalog_cache import get_cached_gateway_catalog
    cached = get_cached_gateway_catalog("openrouter")
    assert cached is not None
    assert len(cached) > 0
```

### Integration Tests
- Test cache invalidation cascade
- Test Redis failover to in-memory
- Test cache warming on startup
- Test concurrent access

### Load Tests
- Compare performance: old vs new cache
- Monitor Redis memory usage
- Test cache eviction under load

---

## Monitoring & Rollback

### Metrics to Track
```python
# New metrics from model_catalog_cache
cache_hits_total{provider="openrouter"}
cache_misses_total{provider="openrouter"}
cache_invalidations_total{provider="openrouter"}
cache_errors_total{provider="openrouter"}
cache_hit_rate{provider="openrouter"}
redis_connection_errors_total
```

### Alerts
- Cache hit rate < 80% (may indicate issues)
- Redis connection errors > 10/min
- Cache invalidation spike (> 100/min)
- Provider fetch errors spike

### Rollback Plan
**If issues detected:**
1. Revert to previous deployment (Phase 1 compatibility layer allows this)
2. Investigate logs and metrics
3. Fix issue in staging
4. Re-deploy with fix

**Rollback Command:**
```bash
# Railway/Vercel: Rollback to previous deployment
railway rollback
# OR
vercel rollback
```

---

## Timeline

### Week 1: Preparation & Compatibility Layer
- ✅ Day 1-2: Analysis & planning (DONE)
- Day 3-4: Implement compatibility layer in cache.py
- Day 5: Test compatibility layer in staging
- Day 6-7: Deploy to production, monitor

### Week 2: High-Priority Providers
- Day 8-10: Migrate 5 high-traffic providers
- Day 11-12: Test & deploy
- Day 13-14: Monitor & fix issues

### Week 3: Medium/Low-Priority Providers + Core Services
- Day 15-18: Migrate 25 remaining providers
- Day 19-20: Migrate core services (models.py, catalog.py, etc.)
- Day 21: Test & deploy

### Week 4: Tests & Verification
- Day 22-24: Update all test files
- Day 25-26: Run full test suite
- Day 27-28: Staging verification

### Week 5: Cleanup
- Day 29-30: Production verification
- Day 31-32: Remove cache.py
- Day 33-34: Documentation update
- Day 35: Final verification

---

## Success Criteria

### Phase Completion Criteria
- ✅ Phase 1: Compatibility layer works, no production errors
- ✅ Phase 2: All provider clients migrated, cache hit rate > 90%
- ✅ Phase 3: Core services migrated, API response times unchanged
- ✅ Phase 4: All tests passing
- ✅ Phase 5: cache.py removed, no imports remain

### Overall Success Metrics
- Zero production incidents related to migration
- Cache hit rate improved by 10%+
- Redis-backed cache operational across all instances
- Monitoring dashboards show healthy cache metrics
- Documentation updated

---

## Dependencies

### Technical Dependencies
- Redis cluster operational and stable
- `model_catalog_cache.py` fully tested
- Monitoring infrastructure (Prometheus/Grafana) configured
- Feature flags available (optional but recommended)

### Team Dependencies
- Backend team reviews all changes
- QA team verifies each phase in staging
- DevOps team monitors production deployment

---

## Open Questions

1. Should we implement feature flags for gradual rollout?
2. Do we need a "dual-write" phase where both caches are updated?
3. Should we keep cache.py as a fallback for 1 month after migration?
4. What's the rollback SLA if issues are detected?

---

## Appendix

### Reference Documentation
- `docs/UNIFIED_CACHE_MIGRATION.md` - Original migration notes
- `src/services/model_catalog_cache.py` - New cache implementation
- `src/cache.py` - Deprecated cache (to be removed)

### Contact
- Owner: Backend Team
- Reviewer: Tech Lead
- Stakeholders: DevOps, QA

---

**Last Updated:** 2026-02-08
**Status:** Plan Complete, Ready for Implementation
