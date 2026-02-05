# Step Logging - Quick Start Guide

## TL;DR

Track complex operations with step-by-step logging:

```python
from src.utils.step_logger import StepLogger

step_logger = StepLogger("My Operation", total_steps=3)
step_logger.start(context="info")

step_logger.step(1, "First step")
# ... do work ...
step_logger.success(count=100)

step_logger.step(2, "Second step")
# ... do work ...
step_logger.success(result="ok")

step_logger.complete(total_items=100)
```

## Log Output Example

```
🚀 START: My Operation (context=info)
▶️  [1/3] First step
✅ [1/3] First step - SUCCESS (count=100, duration_ms=123.4)
▶️  [2/3] Second step
✅ [2/3] Second step - SUCCESS (result=ok, duration_ms=45.2)
🏁 COMPLETE: My Operation (total_items=100, total_duration_ms=168.6)
```

## Common Patterns

### Basic Operation

```python
step_logger = StepLogger("Data Sync", total_steps=2)
step_logger.start(source="api")

step_logger.step(1, "Fetch data", source="api")
data = fetch_data()
step_logger.success(records=len(data))

step_logger.step(2, "Store data", target="database")
store_data(data)
step_logger.success(stored=len(data))

step_logger.complete(total_records=len(data))
```

### With Error Handling

```python
step_logger = StepLogger("Risky Operation", total_steps=2)
step_logger.start()

step_logger.step(1, "Risky step")
try:
    result = risky_operation()
    step_logger.success(status="ok")
except Exception as e:
    step_logger.failure(e, attempted=True)
    raise

step_logger.complete(status="success")
```

### Provider API Fetch Pattern

```python
step_logger = StepLogger("Provider Fetch", total_steps=4)
step_logger.start(provider="openrouter")

# Step 1: Validate
step_logger.step(1, "Validate API key")
if not api_key:
    step_logger.failure(Exception("No API key"))
    return None
step_logger.success(status="valid")

# Step 2: Fetch
step_logger.step(2, "Fetch from API")
response = http.get(url)
step_logger.success(status_code=200, count=len(response.data))

# Step 3: Process
step_logger.step(3, "Process models")
processed = process_models(response.data)
step_logger.success(final_count=len(processed))

# Step 4: Cache
step_logger.step(4, "Cache results")
cache.set(processed)
step_logger.success(cached=len(processed))

step_logger.complete(total_models=len(processed))
```

### Database Query Pattern

```python
step_logger = StepLogger("DB Query", total_steps=2)
step_logger.start(table="models")

# Step 1: Connect
step_logger.step(1, "Connect to database", replica="read")
db = get_connection()
step_logger.success(connection="ready")

# Step 2: Query
step_logger.step(2, "Execute query", table="models")
results = db.query("SELECT * FROM models")
step_logger.success(rows=len(results))

step_logger.complete(total_rows=len(results))
```

### Cache Lookup Pattern

```python
step_logger = StepLogger("Cache Lookup", total_steps=3)
step_logger.start(key="models:all")

# Step 1: Redis
step_logger.step(1, "Check Redis", cache="redis")
redis_data = redis.get(key)
if redis_data:
    step_logger.success(result="HIT", count=len(redis_data))
    step_logger.complete(source="redis")
    return redis_data
step_logger.success(result="MISS")

# Step 2: Local
step_logger.step(2, "Check local cache", cache="memory")
local_data = local_cache.get(key)
if local_data:
    step_logger.success(result="HIT", count=len(local_data))
    step_logger.complete(source="local")
    return local_data
step_logger.success(result="MISS")

# Step 3: Database fallback
step_logger.step(3, "Fetch from database", cache="miss")
db_data = db.query(...)
step_logger.success(rows=len(db_data))

step_logger.complete(source="database", total_rows=len(db_data))
```

## Quick Reference

| Method | Purpose | Example |
|--------|---------|---------|
| `StepLogger(name, total_steps)` | Create logger | `StepLogger("Sync", total_steps=3)` |
| `.start(**metadata)` | Log operation start | `.start(source="api")` |
| `.step(num, name, **metadata)` | Log step start | `.step(1, "Fetch data", timeout=30)` |
| `.success(**results)` | Log step success | `.success(count=100, duration_ms=123)` |
| `.failure(error, **context)` | Log step failure | `.failure(e, attempted=True)` |
| `.skip(reason)` | Log skipped step | `.skip("Already cached")` |
| `.complete(**summary)` | Log operation complete | `.complete(total=100)` |

## Log Symbols

| Symbol | Meaning |
|--------|---------|
| 🚀 | Operation START |
| ▶️  | Step START |
| ✅ | Step SUCCESS |
| ❌ | Step FAILED |
| ⏭️  | Step SKIPPED |
| 🏁 | Operation COMPLETE |

## Tips

✅ **DO**: Include useful metadata in every call
✅ **DO**: Log success AND failure for every step
✅ **DO**: Always call `.complete()` even on errors
✅ **DO**: Use descriptive step names

❌ **DON'T**: Skip calling `.success()` or `.failure()`
❌ **DON'T**: Use generic names like "Step 1"
❌ **DON'T**: Forget to include counts and metrics
❌ **DON'T**: Leave operations incomplete (no `.complete()`)

## Full Example: Model Sync

```python
from src.utils.step_logger import StepLogger

def sync_models_from_provider(provider_name: str):
    """Sync models from a provider with full step logging."""

    step_logger = StepLogger(
        f"{provider_name.title()} Model Sync",
        total_steps=5
    )
    step_logger.start(provider=provider_name)

    try:
        # Step 1: Fetch from provider API
        step_logger.step(1, "Fetch from provider API", provider=provider_name)
        models = fetch_from_provider(provider_name)
        step_logger.success(raw_models=len(models))

        # Step 2: Validate and filter
        step_logger.step(2, "Validate and filter models", input_count=len(models))
        valid_models = validate_models(models)
        step_logger.success(
            valid=len(valid_models),
            filtered=len(models) - len(valid_models)
        )

        # Step 3: Upsert to database
        step_logger.step(3, "Upsert to database", table="models")
        db_results = bulk_upsert_models(valid_models)
        step_logger.success(
            inserted=db_results['inserted'],
            updated=db_results['updated']
        )

        # Step 4: Update cache
        step_logger.step(4, "Update cache", cache_type="redis")
        cache_updated = update_cache(provider_name, valid_models)
        step_logger.success(cache_status="updated", ttl=1800)

        # Step 5: Record metrics
        step_logger.step(5, "Record metrics", provider=provider_name)
        record_sync_metrics(provider_name, len(valid_models))
        step_logger.success(metrics="recorded")

        # Complete
        step_logger.complete(
            provider=provider_name,
            total_models=len(valid_models),
            status="success"
        )

        return {
            'success': True,
            'provider': provider_name,
            'models_synced': len(valid_models)
        }

    except Exception as e:
        step_logger.failure(e, provider=provider_name)
        step_logger.complete(status="failed", error=str(e))
        raise
```

## Output of Full Example

```log
[INFO] 🚀 START: Openrouter Model Sync (provider=openrouter)
[INFO] ▶️  [1/5] Fetch from provider API (provider=openrouter)
[INFO] ✅ [1/5] Fetch from provider API - SUCCESS (raw_models=2834, duration_ms=1523.7)
[INFO] ▶️  [2/5] Validate and filter models (input_count=2834)
[INFO] ✅ [2/5] Validate and filter models - SUCCESS (valid=2756, filtered=78, duration_ms=234.5)
[INFO] ▶️  [3/5] Upsert to database (table=models)
[INFO] ✅ [3/5] Upsert to database - SUCCESS (inserted=120, updated=2636, duration_ms=3456.2)
[INFO] ▶️  [4/5] Update cache (cache_type=redis)
[INFO] ✅ [4/5] Update cache - SUCCESS (cache_status=updated, ttl=1800, duration_ms=45.3)
[INFO] ▶️  [5/5] Record metrics (provider=openrouter)
[INFO] ✅ [5/5] Record metrics - SUCCESS (metrics=recorded, duration_ms=12.1)
[INFO] 🏁 COMPLETE: Openrouter Model Sync (provider=openrouter, total_models=2756, status=success, total_duration_ms=5271.8)
```

---

**Quick Start By**: Claude Code Assistant
**Last Updated**: 2026-02-04
