# Database-First Architecture Implementation Plan

**Issue**: #980 - Make database single source of truth for both sync and cache

**Date**: 2026-01-28

## Current Architecture (Problem)

### Dual System Issue

Currently, two parallel systems exist for model data:

```
Provider APIs
    ↓
    ├─→ Sync System → Database (via admin endpoints)
    └─→ Cache System → get_cached_models() → fetch_models_from_*() → Provider APIs (direct calls)
            ↓
        API Responses
```

### Key Problems Identified

1. **Direct Provider API Calls in Cache Layer**:
   - `get_cached_models(gateway)` in `src/services/models.py:772`
   - When cache miss occurs, it calls `fetch_models_from_*()` functions
   - These functions make direct HTTP requests to provider APIs
   - Example flow: `get_cached_models("openrouter")` → cache miss → `fetch_models_from_openrouter()` → HTTP call

2. **Duplicate API Calls**:
   - Sync system calls provider APIs to populate database
   - Cache system also calls provider APIs independently
   - No coordination between the two systems

3. **Inconsistent Data**:
   - Database may have different models than what's in cache
   - No guarantee of consistency between DB and cache
   - Race conditions possible during concurrent access

4. **Multiple Functions Affected**:
   - `get_all_models_parallel()` - line 575
   - `get_all_models_sequential()` - line 680
   - `get_cached_models()` - line 772
   - All 30+ `fetch_models_from_*()` functions in models.py
   - Catalog routes in `src/routes/catalog.py`

## Proposed Architecture (Solution)

```
Provider APIs
    ↓
Sync System (admin endpoints only)
    ↓
Database (models + providers tables) ← Single Source of Truth
    ↓
New Cache Layer (Redis + In-Memory)
    ↓
API Responses
```

### Benefits

✅ **Consistency**: Database is authoritative source
✅ **Performance**: No duplicate API calls to providers
✅ **Reliability**: Fewer points of failure
✅ **Maintainability**: Clear separation of concerns
✅ **Debuggability**: Easier to trace data flow

## Implementation Plan

### Phase 1: Database Layer Enhancement (Week 1)

#### 1.1 Add New Database Query Functions

Create optimized queries in `src/db/models_catalog_db.py`:

```python
def get_all_models_for_catalog(
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get all models optimized for catalog building.
    Returns models with provider info and pricing.
    """
    pass

def get_models_by_gateway(
    gateway_slug: str,
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get models for a specific gateway/provider.
    Optimized for single-gateway catalog requests.
    """
    pass
```

#### 1.2 Add Database Indexes

Create migration in `supabase/migrations/`:

```sql
-- Index for provider lookups
CREATE INDEX IF NOT EXISTS idx_models_provider_active
ON models(provider_id, is_active);

-- Index for model_id lookups (used in routing)
CREATE INDEX IF NOT EXISTS idx_models_model_id
ON models(model_id);

-- Index for provider slug lookups
CREATE INDEX IF NOT EXISTS idx_models_provider_slug
ON models((providers.slug)) WHERE is_active = true;
```

### Phase 2: New Cache Layer (Week 1-2)

#### 2.1 Update model_catalog_cache.py

Add new functions to cache database reads:

```python
def get_models_from_db_cached(
    provider_slug: str | None = None,
    include_inactive: bool = False
) -> list[dict[str, Any]]:
    """
    Get models from database with Redis caching.

    Flow:
    1. Check Redis cache
    2. If miss, query database
    3. Cache result in Redis
    4. Return data
    """
    cache_key = f"models:db:{provider_slug or 'all'}:{include_inactive}"

    # Check cache
    cached = redis_get(cache_key)
    if cached:
        return json.loads(cached)

    # Query database
    if provider_slug:
        models = get_models_by_provider_slug(provider_slug, not include_inactive)
    else:
        models = get_all_models_for_catalog(include_inactive)

    # Cache for 15 minutes (same as current TTL_FULL_CATALOG)
    redis_set(cache_key, json.dumps(models), ttl=900)

    return models
```

#### 2.2 Convert Database Models to API Format

Create transformation function:

```python
def transform_db_model_to_api_format(db_model: dict) -> dict:
    """
    Convert database model format to API response format.

    Database model has:
    - id (int)
    - model_id (str)
    - model_name (str)
    - provider_id (int)
    - providers (dict) - joined data
    - pricing_prompt (decimal)
    - pricing_completion (decimal)
    - context_length (int)
    - etc.

    API format needs:
    - id (str) - the model_id
    - name (str)
    - source_gateway (str)
    - provider_slug (str)
    - context_length (int)
    - pricing (dict)
    - etc.
    """
    pass
```

### Phase 3: Update Services Layer (Week 2)

#### 3.1 Create New get_models_from_db Function

In `src/services/models.py`, create replacement for `get_cached_models`:

```python
def get_models_from_db(
    gateway: str | None = None,
    include_inactive: bool = False
) -> list[dict]:
    """
    Get models from database (single source of truth).

    Replaces get_cached_models() which called provider APIs directly.

    Args:
        gateway: Gateway/provider slug filter
        include_inactive: Include inactive models

    Returns:
        List of models in API format
    """
    # Get from database (with Redis caching)
    db_models = get_models_from_db_cached(gateway, include_inactive)

    # Transform to API format
    api_models = [transform_db_model_to_api_format(m) for m in db_models]

    return api_models
```

#### 3.2 Deprecate Direct Provider API Calls

Mark old functions for removal:

```python
@deprecated("Use sync endpoints instead. Do not call provider APIs directly.")
def fetch_models_from_openrouter():
    """
    DEPRECATED: Only use in sync operations.
    For normal catalog requests, use get_models_from_db().
    """
    pass
```

### Phase 4: Update Routes (Week 2-3)

#### 4.1 Update catalog.py

Replace all `get_cached_models()` calls with `get_models_from_db()`:

```python
# Before (BAD):
@router.get("/models")
async def get_models(gateway: str = "all"):
    if gateway == "all":
        models = get_all_models_parallel()  # Calls provider APIs
    else:
        models = get_cached_models(gateway)  # Calls provider APIs
    return models

# After (GOOD):
@router.get("/models")
async def get_models(gateway: str = "all"):
    if gateway == "all":
        models = get_models_from_db()  # Reads from database
    else:
        models = get_models_from_db(gateway=gateway)  # Reads from database
    return models
```

### Phase 5: Sync Endpoint Cache Invalidation (Week 3)

#### 5.1 Update model_catalog_sync.py

Add cache invalidation after database updates:

```python
async def sync_provider_models(provider_slug: str):
    """
    Sync models from provider API to database.
    """
    # Fetch from provider API
    models = await fetch_models_from_provider_api(provider_slug)

    # Save to database
    save_models_to_db(provider_slug, models)

    # Invalidate caches
    invalidate_provider_catalog(provider_slug)  # Redis cache
    invalidate_full_catalog()  # Full catalog cache

    return {"synced": len(models)}
```

### Phase 6: Testing (Week 3-4)

#### 6.1 Unit Tests

Test new database-first functions:

```python
def test_get_models_from_db_cached():
    """Test database reads with caching"""
    pass

def test_transform_db_model_to_api_format():
    """Test model transformation"""
    pass

def test_cache_invalidation_on_sync():
    """Test cache is invalidated after sync"""
    pass
```

#### 6.2 Integration Tests

Test full flow:

```python
def test_catalog_endpoint_uses_database():
    """Verify catalog endpoint reads from database, not provider APIs"""
    pass

def test_sync_updates_database_and_cache():
    """Verify sync updates both database and cache"""
    pass
```

#### 6.3 Performance Tests

Benchmark response times:

```python
def test_catalog_response_time():
    """Ensure response times are acceptable (<100ms)"""
    pass
```

## Files to Modify

### High Priority

1. ✅ `src/db/models_catalog_db.py` - Add optimized query functions
2. ✅ `src/services/model_catalog_cache.py` - Add database caching functions
3. ✅ `src/services/models.py` - Create get_models_from_db, deprecate old functions
4. ✅ `src/routes/catalog.py` - Replace get_cached_models with get_models_from_db

### Medium Priority

5. `src/services/model_catalog_sync.py` - Add cache invalidation
6. `src/services/model_availability.py` - Use database reads
7. `src/services/multi_provider_registry.py` - Use database reads

### Low Priority

8. Various route handlers that fetch models
9. Background tasks that build catalogs

## Migration Strategy

### Step 1: Feature Flag

Add feature flag to toggle between old and new system:

```python
USE_DB_FIRST_CATALOG = os.getenv("USE_DB_FIRST_CATALOG", "false").lower() == "true"

def get_models(gateway: str):
    if USE_DB_FIRST_CATALOG:
        return get_models_from_db(gateway)
    else:
        return get_cached_models(gateway)  # Old system
```

### Step 2: Gradual Rollout

1. Deploy with feature flag OFF
2. Run side-by-side comparison tests
3. Enable for 10% of requests
4. Monitor error rates and performance
5. Gradually increase to 100%
6. Remove old code and feature flag

### Step 3: Rollback Plan

If issues occur:
1. Set feature flag to OFF
2. Old code path still available
3. Quick rollback without code changes

## Performance Considerations

### Database Query Optimization

- Add indexes for common queries
- Use appropriate JOIN strategy
- Limit result sets with pagination
- Use database connection pooling

### Caching Strategy

| Cache Type | TTL | When to Use |
|------------|-----|-------------|
| Full catalog | 15 min | `/models?gateway=all` |
| Single provider | 30 min | `/models?gateway=openrouter` |
| Individual model | 60 min | `/models/{model_id}` |

### Expected Performance

- Current: 500ms-2s (with provider API calls)
- Target: 5-50ms (with database + Redis cache)
- Improvement: 90-99% faster

## Success Metrics

### Acceptance Criteria

- [ ] All catalog endpoints use database reads
- [ ] No direct provider API calls in non-sync code
- [ ] Response times < 100ms for cached requests
- [ ] Response times < 500ms for cache misses
- [ ] No increase in error rates
- [ ] Cache hit rate > 80%
- [ ] All tests passing
- [ ] Documentation updated

### Monitoring

- Track cache hit/miss rates
- Monitor database query times
- Track API response times
- Monitor error rates
- Alert on performance degradation

## Timeline

- **Week 1**: Database layer + cache layer
- **Week 2**: Services layer + routes
- **Week 3**: Sync endpoints + testing
- **Week 4**: Performance testing + rollout

## Related Issues

- #978 - Flush endpoints
- #979 - Model name standardization
- Audit report: `docs/MODEL_SYNC_AUDIT.md`

## References

- Database schema: `supabase/migrations/`
- Current cache: `src/services/model_catalog_cache.py`
- Current models service: `src/services/models.py`
- Sync system: `src/services/model_catalog_sync.py`
