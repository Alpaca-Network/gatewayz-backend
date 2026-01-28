# Model & Pricing Sync Guide

Complete guide for syncing models from provider APIs to the database, making them available to the frontend.

---

## 🔄 Overview

Gatewayz has **two parallel sync systems**:

1. **Model Catalog Sync** - Syncs model metadata (name, context length, capabilities, etc.)
2. **Pricing Sync** - Syncs model pricing from provider APIs to the database

Both systems write to the database (`models` and `model_pricing` tables) which the frontend uses.

---

## ⚙️ Automatic Background Sync (Already Running!)

### Pricing Sync Scheduler

**Status**: ✅ **Already enabled and running automatically**

The pricing sync scheduler starts automatically on application startup and runs every **6 hours by default**.

**Configuration** (`src/config/config.py`):
```python
# Environment Variables
PRICING_SYNC_ENABLED="true"              # Enable/disable automatic sync
PRICING_SYNC_INTERVAL_HOURS="6"          # Sync interval (default: 6 hours)
PRICING_SYNC_PROVIDERS="openrouter,featherless,nearai,alibaba-cloud"
```

**How it works** (see `src/services/startup.py:303-320`):
- Starts on app startup
- Runs pricing sync every N hours
- Auto-cleanup of stuck syncs every 15 minutes
- Exports Prometheus metrics for monitoring
- Graceful shutdown on app termination

**Logs to watch**:
```
📅 Pricing sync scheduler started (interval: 6h = 21600s)
🔄 Starting scheduled pricing sync...
✅ Scheduled pricing sync completed successfully (updated: 1234 models)
```

---

## 🚀 Manual Sync Methods

### Option 1: Admin API Endpoints

#### Model Sync Endpoints

**Base URL**: `/admin/model-sync`

```bash
# 1. List available providers
curl -X GET "https://api.gatewayz.ai/admin/model-sync/providers"

# 2. Sync specific provider
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter"

# 3. Sync all providers
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all"

# 4. Sync specific providers only
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all?providers=openrouter&providers=deepinfra"

# 5. Dry-run (test without changes)
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter?dry_run=true"

# 6. Check sync status
curl -X GET "https://api.gatewayz.ai/admin/model-sync/status"
```

#### Pricing Sync Endpoints

**Base URL**: `/pricing/sync` or `/admin/pricing/sync`

```bash
# 1. Dry-run pricing sync (see what would change)
curl -X POST "https://api.gatewayz.ai/pricing/sync/dry-run"

# 2. Run actual pricing sync (with admin key)
curl -X POST "https://api.gatewayz.ai/admin/pricing/sync/b23a0fbc-8b0e-4b9c-b552-d82f69d28486"

# 3. Sync specific provider
curl -X POST "https://api.gatewayz.ai/pricing/sync/run/openrouter"

# 4. Background sync (returns immediately with job ID)
curl -X POST "https://api.gatewayz.ai/pricing/sync/run?background=true"

# 5. Check sync job status
curl -X GET "https://api.gatewayz.ai/admin/pricing/sync/b23a0fbc-8b0e-4b9c-b552-d82f69d28486/status/{JOB_ID}"

# 6. Get sync history
curl -X GET "https://api.gatewayz.ai/pricing/sync/history?limit=10"

# 7. Get scheduler status
curl -X GET "https://api.gatewayz.ai/admin/pricing/sync/b23a0fbc-8b0e-4b9c-b552-d82f69d28486/scheduler/status"
```

### Option 2: Python Scripts

#### Model Sync Script

**Location**: `scripts/sync_models.py`

```bash
# Sync all providers
python scripts/sync_models.py

# Sync specific provider
python scripts/sync_models.py --provider openrouter

# Dry run
python scripts/sync_models.py --dry-run
```

#### Pricing Sync Scripts

**Manual sync**:
```bash
python scripts/manual_sync_now.py
```

**Trigger via API**:
```bash
python scripts/trigger_pricing_sync.py
```

**Check sync status**:
```bash
python scripts/check_sync_status.py
```

**Clear stuck syncs**:
```bash
python scripts/clear_stuck_sync.py
```

### Option 3: Direct Python API

```python
# Model Catalog Sync
from src.services.model_catalog_sync import sync_provider_models, sync_all_providers

# Sync single provider
result = sync_provider_models("openrouter", dry_run=False)
print(f"Synced {result['models_synced']} models")

# Sync all providers
result = sync_all_providers()
print(f"Total synced: {result['total_models_synced']}")
```

```python
# Pricing Sync
from src.services.pricing_sync_scheduler import trigger_manual_sync, queue_background_sync

# Immediate sync (blocks until complete)
result = await trigger_manual_sync()

# Background sync (returns job ID immediately)
job_id = await queue_background_sync(triggered_by="manual")
print(f"Sync job queued: {job_id}")
```

---

## ⏱️ Sync Frequency Configuration

### Current Default Settings

```bash
# .env or environment variables
PRICING_SYNC_ENABLED=true
PRICING_SYNC_INTERVAL_HOURS=6          # Every 6 hours
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud
```

### Recommended Sync Frequencies

| Use Case | Interval | Configuration |
|----------|----------|---------------|
| Development | 1 hour | `PRICING_SYNC_INTERVAL_HOURS=1` |
| Staging | 3 hours | `PRICING_SYNC_INTERVAL_HOURS=3` |
| Production | 6 hours | `PRICING_SYNC_INTERVAL_HOURS=6` (default) |
| Critical pricing updates | Manual | Use API endpoints |

### How to Change Sync Frequency

**Option 1: Environment Variable**
```bash
# In .env or Railway/Vercel environment
PRICING_SYNC_INTERVAL_HOURS=2  # Sync every 2 hours
```

**Option 2: Runtime Configuration** (future enhancement)
```bash
curl -X POST "https://api.gatewayz.ai/pricing/sync/schedule?interval_hours=2&enabled=true"
```

---

## 📊 Monitoring Sync Operations

### Database Tables

**Model Sync**:
- `models` - Model metadata
- `model_pricing` - Pricing data
- `providers` - Provider configurations

**Sync Tracking**:
- `pricing_sync_jobs` - Background job tracking
- `pricing_sync_log` - Historical sync operations
- `pricing_sync_lock` - Distributed lock (prevents concurrent syncs)
- `model_pricing_history` - Audit trail of pricing changes

### Prometheus Metrics

Available at `/metrics`:
```
pricing_scheduled_sync_runs_total{status="success|failed"}
pricing_scheduled_sync_duration_seconds
pricing_last_sync_timestamp{provider="openrouter"}
pricing_models_synced_total{provider="openrouter",status="updated|skipped|unchanged"}
```

### Log Monitoring

```bash
# Watch sync logs
tail -f logs/pricing_sync.log

# In Railway/Vercel
railway logs --filter="pricing sync"
```

---

## 🔧 Advanced Usage

### Custom Provider List

Only sync specific providers:
```python
# Environment variable
PRICING_SYNC_PROVIDERS=openrouter,deepinfra,groq

# Or via API
curl -X POST "/admin/model-sync/all?providers=openrouter&providers=deepinfra"
```

### Sync with Validation

```python
from src.services.model_catalog_sync import sync_provider_models

# Sync with validation
result = sync_provider_models("openrouter", dry_run=False)

if result["success"]:
    print(f"✅ Synced {result['models_synced']} models")
    print(f"   Fetched: {result['models_fetched']}")
    print(f"   Transformed: {result['models_transformed']}")
    print(f"   Skipped: {result['models_skipped']}")
else:
    print(f"❌ Sync failed: {result.get('error')}")
```

### Background Job Tracking

```python
from src.services.pricing_sync_scheduler import queue_background_sync, get_sync_job_status

# Queue background sync
job_id = await queue_background_sync(triggered_by="admin")

# Poll for status
while True:
    status = await get_sync_job_status(job_id)
    if status["status"] in ["completed", "failed"]:
        break
    await asyncio.sleep(5)

print(f"Job {job_id}: {status['status']}")
print(f"Models updated: {status['models_updated']}")
```

---

## 🚨 Troubleshooting

### Issue: Stale Data in Frontend

**Solution**: Invalidate caches after sync

```python
from src.cache import clear_models_cache
from src.services.model_catalog_cache import invalidate_full_catalog, invalidate_provider_catalog

# After model sync
clear_models_cache("openrouter")              # Clear in-memory cache
invalidate_provider_catalog("openrouter")     # Clear Redis provider cache
invalidate_full_catalog()                     # Clear Redis full catalog
```

**Note**: The sync services automatically invalidate caches (see `src/services/model_catalog_sync.py:609-624`)

### Issue: Stuck Syncs

**Symptom**: Sync jobs remain in "running" state forever

**Solution**: Run cleanup script
```bash
python scripts/clear_stuck_sync.py

# Or via API
curl -X POST "https://api.gatewayz.ai/admin/pricing/sync/cleanup"
```

**Automatic cleanup**: Runs every 15 minutes (see `src/services/startup.py:287-298`)

### Issue: Database Connection Errors

**Check**:
1. `SUPABASE_URL` and `SUPABASE_KEY` environment variables
2. Database is accessible from the server
3. `models` and `model_pricing` tables exist

**Verify**:
```bash
# Test database connection
python -c "from src.config.supabase_config import get_supabase_client; client = get_supabase_client(); print('✅ Connected')"
```

### Issue: Provider API Rate Limits

**Solution**: Increase sync interval or sync providers separately

```bash
# Sync providers one at a time with delays
curl -X POST "/admin/model-sync/provider/openrouter"
sleep 60
curl -X POST "/admin/model-sync/provider/deepinfra"
sleep 60
curl -X POST "/admin/model-sync/provider/featherless"
```

---

## 📝 Database Schema

### Models Table

```sql
CREATE TABLE models (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER REFERENCES providers(id),
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    provider_model_id TEXT NOT NULL,
    description TEXT,
    context_length INTEGER,
    modality TEXT DEFAULT 'text->text',
    architecture TEXT,
    -- Capabilities
    supports_streaming BOOLEAN DEFAULT false,
    supports_function_calling BOOLEAN DEFAULT false,
    supports_vision BOOLEAN DEFAULT false,
    -- Status
    is_active BOOLEAN DEFAULT true,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Model Pricing Table

```sql
CREATE TABLE model_pricing (
    id BIGSERIAL PRIMARY KEY,
    model_id BIGINT REFERENCES models(id) ON DELETE CASCADE,
    price_per_input_token NUMERIC(20, 15) NOT NULL DEFAULT 0,
    price_per_output_token NUMERIC(20, 15) NOT NULL DEFAULT 0,
    price_per_image_token NUMERIC(20, 15),
    price_per_request NUMERIC(10, 6),
    pricing_source TEXT DEFAULT 'provider',
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 🎯 Best Practices

1. **Development**: Use `dry_run=true` first to preview changes
2. **Production**: Let automatic scheduler handle syncs
3. **Emergency updates**: Use manual sync endpoints for critical pricing changes
4. **Monitoring**: Set up alerts on `pricing_scheduled_sync_runs_total{status="failed"}`
5. **Performance**: Use background sync for API endpoints (avoids timeouts)
6. **Validation**: Check `pricing_sync_log` table for sync history
7. **Cache management**: Trust automatic cache invalidation after syncs

---

## 📚 Related Files

### Services
- `src/services/model_catalog_sync.py` - Model sync service
- `src/services/pricing_sync_service.py` - Pricing sync service
- `src/services/pricing_sync_scheduler.py` - Automatic scheduler
- `src/services/pricing_sync_jobs.py` - Background job management
- `src/services/pricing_sync_cleanup.py` - Cleanup stuck syncs
- `src/services/pricing_sync_lock.py` - Distributed locking

### Routes
- `src/routes/model_sync.py` - Model sync API endpoints
- `src/routes/pricing_sync.py` - Pricing sync API endpoints
- `src/routes/pricing_sync_routes.py` - Additional pricing endpoints

### Database
- `src/db/models_catalog_db.py` - Model database operations
- `supabase/migrations/20260119120000_create_model_pricing_table.sql`
- `supabase/migrations/20260126000001_add_pricing_sync_tables.sql`

### Configuration
- `src/config/config.py` - Environment configuration
- `src/services/startup.py` - Application startup/lifespan

---

## 🆘 Support

For issues or questions:
1. Check sync logs: `tail -f logs/pricing_sync.log`
2. Check database: Query `pricing_sync_log` table
3. Monitor metrics: `/metrics` endpoint
4. Review this guide: `docs/MODEL_SYNC_GUIDE.md`

---

**Last Updated**: 2026-01-27
**Version**: 2.0.3
