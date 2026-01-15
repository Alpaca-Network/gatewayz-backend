# Provider & Model Synchronization System

This document explains how providers and models are kept synchronized in the Gatewayz system.

## 📋 Table of Contents

- [Overview](#overview)
- [Two-Tier Sync System](#two-tier-sync-system)
- [Provider Sync](#provider-sync)
- [Model Sync](#model-sync)
- [Configuration](#configuration)
- [Manual Operations](#manual-operations)
- [Troubleshooting](#troubleshooting)

---

## Overview

**The Challenge**:
- **Providers** should always match your codebase (`GATEWAY_REGISTRY` in `src/routes/catalog.py`)
- **Models** should reflect the latest offerings from each provider's API (which you don't control)

**The Solution**:
A two-tier synchronization system that handles each concern separately.

---

## Two-Tier Sync System

```
┌─────────────────────────────────────────────────────────────────┐
│                     SYNCHRONIZATION FLOW                        │
└─────────────────────────────────────────────────────────────────┘

TIER 1: PROVIDER SYNC (Code → Database)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Source: src/routes/catalog.py::GATEWAY_REGISTRY
Frequency: On every deployment + app startup
Method: Automatic (migration + startup hook)

  GATEWAY_REGISTRY (28 providers)
           ↓
     Migration SQL
           ↓
    providers table ← Always up-to-date with code


TIER 2: MODEL SYNC (Provider APIs → Database)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Source: Provider APIs (OpenRouter, OpenAI, etc.)
Frequency: Every 6 hours (configurable) + manual triggers
Method: Background tasks + scheduled jobs

  Provider APIs (337+ models)
           ↓
  Model fetch functions
           ↓
    models table ← Updated every 6 hours
```

---

## Provider Sync

### 🎯 Goal
Ensure the `providers` table always matches `GATEWAY_REGISTRY` in your code.

### 🔄 How It Works

#### 1. **On Git Push** (Database Migration)
```sql
-- supabase/migrations/20260115000000_sync_providers_from_gateway_registry.sql
INSERT INTO providers (name, slug, description, ...)
VALUES
  ('OpenAI', 'openai', ...),
  ('Anthropic', 'anthropic', ...),
  ...
ON CONFLICT (slug) DO UPDATE SET ...
```

When you push to main:
1. GitHub Actions runs database migrations
2. Migration upserts all 28 providers
3. Database is now synced with code

#### 2. **On App Startup** (Runtime Sync)
```python
# src/services/startup.py
async def lifespan(app):
    # Sync providers from GATEWAY_REGISTRY
    result = await sync_providers_on_startup()
    # ✅ Providers table now matches code
```

This provides **double assurance**: even if migrations didn't run, startup sync catches it.

### 📝 Adding a New Provider

**Step 1**: Add to `GATEWAY_REGISTRY`
```python
# src/routes/catalog.py
GATEWAY_REGISTRY = {
    "my-new-provider": {
        "name": "My New Provider",
        "color": "bg-purple-500",
        "priority": "fast",
        "site_url": "https://mynewprovider.com",
    },
    # ... existing providers
}
```

**Step 2**: Create provider migration (optional but recommended)
```bash
# This keeps migration history clean
cat > supabase/migrations/20260115000001_add_my_provider.sql <<EOF
INSERT INTO "public"."providers" (name, slug, description, ...)
VALUES ('My New Provider', 'my-new-provider', ...)
ON CONFLICT (slug) DO UPDATE SET ...;
EOF
```

**Step 3**: Push to Git
```bash
git add src/routes/catalog.py supabase/migrations/
git commit -m "feat: add MyNewProvider gateway"
git push origin main
```

**Result**:
- ✅ Migration runs → providers table updated
- ✅ App restarts → startup sync confirms it
- ✅ Frontend fetches `/gateways` → sees new provider

---

## Model Sync

### 🎯 Goal
Keep the `models` table updated with the latest models from each provider's API.

### 🔄 How It Works

#### 1. **On App Startup** (Optional High-Priority Sync)
```python
# Controlled by env var: SYNC_MODELS_ON_STARTUP=true
high_priority_providers = ["openrouter", "openai", "anthropic", "groq"]
result = await sync_initial_models_on_startup(high_priority_providers)
# ✅ Critical models available immediately
```

**Default**: `SYNC_MODELS_ON_STARTUP=false` for faster startup.

#### 2. **Background Task** (Every 6 Hours)
```python
# Runs in background automatically
async def periodic_model_sync_task(interval_hours=6):
    while True:
        await asyncio.sleep(interval_hours * 3600)
        await sync_models_from_providers()  # Syncs ALL providers
```

**Configurable** via `MODEL_SYNC_INTERVAL_HOURS` env var.

#### 3. **Scheduled GitHub Actions** (Every 6 Hours)
```yaml
# .github/workflows/model-sync.yml
on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
```

**Why both?**:
- Background task: keeps running app updated
- GitHub Actions: ensures sync even if app restarts

#### 4. **Manual API Trigger**
```bash
# Sync all providers
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all"

# Sync specific provider
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter"

# Dry run (see what would change)
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all?dry_run=true"
```

### 📋 Model Sync Process

```python
# For each provider:
1. Fetch models from provider API
   └─ fetch_models_from_openrouter()
   └─ fetch_models_from_fireworks()
   └─ ... (28 providers total)

2. Transform to database schema
   └─ Normalize model_id, pricing, capabilities
   └─ Extract metadata (context_length, modality, etc.)

3. Upsert to database
   └─ INSERT ... ON CONFLICT (provider_id, model_id) DO UPDATE
   └─ Creates new models, updates existing
```

**Result**: 337+ models always reflect latest provider offerings.

---

## Configuration

### Environment Variables

```bash
# ===== Provider Sync (always enabled) =====
# No config needed - syncs on startup automatically

# ===== Model Sync =====

# Sync models on startup (default: false for faster startup)
SYNC_MODELS_ON_STARTUP=false

# Background sync interval in hours (default: 6)
MODEL_SYNC_INTERVAL_HOURS=6
```

### Provider Sync Configuration

Edit `src/routes/catalog.py`:
```python
GATEWAY_REGISTRY = {
    "provider-slug": {
        "name": "Display Name",
        "color": "bg-blue-500",      # Tailwind color class
        "priority": "fast",           # "fast" or "slow"
        "site_url": "https://...",   # Provider website
        "aliases": ["alt-name"],     # Optional aliases
    }
}
```

### Model Fetch Functions

Register in `src/services/model_catalog_sync.py`:
```python
PROVIDER_FETCH_FUNCTIONS = {
    "my-provider": fetch_models_from_my_provider,
    # ...
}
```

Then implement:
```python
# src/services/my_provider_client.py
def fetch_models_from_my_provider() -> list[dict]:
    """Fetch models from MyProvider API"""
    response = requests.get("https://api.myprovider.com/models")
    models = response.json()

    # Normalize to standard format
    return [
        {
            "id": model["id"],
            "name": model["name"],
            "context_length": model["max_tokens"],
            "pricing": {
                "prompt": model["price_per_1k_prompt"],
                "completion": model["price_per_1k_completion"],
            },
            "supports_streaming": True,
            "source_gateway": "my-provider",
        }
        for model in models
    ]
```

---

## Manual Operations

### Seed Providers Manually

```bash
# From project root
python scripts/database/seed_providers.py

# Dry run (see what would change)
python scripts/database/seed_providers.py --dry-run
```

### Trigger Model Sync Manually

**Option 1: Via API**
```bash
# All providers
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all" \
  -H "Authorization: Bearer YOUR_ADMIN_KEY"

# Single provider
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter" \
  -H "Authorization: Bearer YOUR_ADMIN_KEY"
```

**Option 2: Via GitHub Actions**
1. Go to **Actions** tab in GitHub
2. Select **Scheduled Model Sync** workflow
3. Click **Run workflow**
4. (Optional) Specify providers: `openrouter,openai,anthropic`

**Option 3: Via Python Script**
```bash
# From project root
python - <<EOF
import asyncio
from src.services.provider_model_sync_service import trigger_full_sync

result = asyncio.run(trigger_full_sync())
print(f"Synced {result['models']['total_models_synced']} models")
EOF
```

### View Sync Status

```bash
# Check last model sync time
curl "https://api.gatewayz.ai/admin/model-sync/status"

# Response:
{
  "last_sync": "2026-01-15T10:30:00Z",
  "providers_count": 28,
  "models_count": 337,
  "sync_interval_hours": 6,
  "next_sync_eta": "2026-01-15T16:30:00Z"
}
```

---

## Troubleshooting

### Problem: Provider Not Showing Up

**Symptom**: Added provider to `GATEWAY_REGISTRY` but not in database.

**Solution**:
```bash
# Check if migration ran
SELECT * FROM providers WHERE slug = 'my-provider';

# If not found, run seed script
python scripts/database/seed_providers.py

# Or trigger startup sync
# Restart the application
```

### Problem: Models Out of Date

**Symptom**: Provider added new models but they're not in database.

**Solution**:
```bash
# Check last sync time
curl https://api.gatewayz.ai/admin/model-sync/status

# Trigger immediate sync
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/openrouter

# Or wait for next scheduled sync (every 6h)
```

### Problem: Model Sync Failing

**Symptom**: Logs show model sync errors.

**Check**:
1. **API Keys**: Ensure provider API keys are set
   ```bash
   echo $OPENROUTER_API_KEY  # Should not be empty
   ```

2. **Provider API Status**: Check if provider API is down
   ```bash
   curl https://openrouter.ai/api/v1/models
   ```

3. **Database Connectivity**: Verify Supabase connection
   ```bash
   python - <<EOF
   from src.config.supabase_config import get_supabase_client
   client = get_supabase_client()
   print(client.table("providers").select("count").execute())
   EOF
   ```

**Debug**:
```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Run single provider sync with verbose output
python - <<EOF
import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

from src.services.model_catalog_sync import sync_provider_models
result = sync_provider_models("openrouter", dry_run=True)
print(result)
EOF
```

### Problem: Duplicate Models

**Symptom**: Same model appears multiple times in database.

**Cause**: Usually happens when `provider_id` + `model_id` uniqueness breaks.

**Solution**:
```sql
-- Check for duplicates
SELECT model_id, provider_id, COUNT(*) as count
FROM models
GROUP BY model_id, provider_id
HAVING COUNT(*) > 1;

-- Remove duplicates (keep most recent)
DELETE FROM models a USING models b
WHERE a.id < b.id
  AND a.model_id = b.model_id
  AND a.provider_id = b.provider_id;
```

### Problem: Slow Startup

**Symptom**: App takes long to start.

**Cause**: `SYNC_MODELS_ON_STARTUP=true` syncs many models on startup.

**Solution**:
```bash
# Disable startup sync (use background sync instead)
export SYNC_MODELS_ON_STARTUP=false

# Models will be synced by background task (every 6h)
# Or trigger manual sync after startup
```

---

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────┐
│                    GATEWAYZ SYNC SYSTEM                        │
└────────────────────────────────────────────────────────────────┘

┌──────────────────┐
│ GATEWAY_REGISTRY │ (28 providers defined in code)
│ src/routes/      │
│ catalog.py       │
└────────┬─────────┘
         │
         ├──────────────────┬───────────────────┐
         ↓                  ↓                   ↓
  ┌─────────────┐   ┌─────────────┐   ┌──────────────┐
  │ Migration   │   │ Startup     │   │ Seed Script  │
  │ (on push)   │   │ Sync        │   │ (manual)     │
  └──────┬──────┘   └──────┬──────┘   └──────┬───────┘
         │                  │                  │
         └──────────────────┴──────────────────┘
                            ↓
                  ┌─────────────────────┐
                  │  providers table    │ ← Always synced
                  │  (28 rows)          │
                  └─────────────────────┘


┌──────────────────────────────────────────────────────────────┐
│ Provider APIs (OpenRouter, OpenAI, Anthropic, etc.)         │
│ https://openrouter.ai/api/v1/models                          │
│ https://api.openai.com/v1/models                             │
│ ... (28 providers)                                           │
└────────┬─────────────────────────────────────────────────────┘
         │
         ├────────────────┬────────────────┬──────────────────┐
         ↓                ↓                ↓                  ↓
  ┌──────────┐   ┌──────────────┐  ┌────────────┐  ┌─────────────┐
  │ Startup  │   │ Background   │  │ GitHub     │  │ Manual API  │
  │ (optional)  │ │ Task (6h)    │  │ Actions(6h)│  │ Trigger     │
  └────┬─────┘   └──────┬───────┘  └─────┬──────┘  └──────┬──────┘
       │                │                 │                │
       └────────────────┴─────────────────┴────────────────┘
                            ↓
              ┌────────────────────────────┐
              │ Model Sync Service         │
              │ - Fetch from provider APIs │
              │ - Transform to DB schema   │
              │ - Upsert to database       │
              └────────────┬───────────────┘
                           ↓
                  ┌─────────────────────┐
                  │  models table       │ ← Updated every 6h
                  │  (337+ rows)        │
                  └─────────────────────┘
```

---

## Summary

### ✅ Providers (Code → Database)
- **Source**: `GATEWAY_REGISTRY` in code
- **Sync**: On every push (migration) + app startup
- **Always current**: Database matches code

### ✅ Models (Provider APIs → Database)
- **Source**: Provider APIs (you don't control)
- **Sync**: Every 6 hours (background + scheduled)
- **Fresh data**: Database reflects latest provider offerings

### 🎯 Best Practices
1. **Adding providers**: Update `GATEWAY_REGISTRY` → push → automatic sync
2. **Model updates**: Happen automatically every 6 hours
3. **Manual sync**: Use API or GitHub Actions for immediate updates
4. **Monitoring**: Check `/admin/model-sync/status` for sync health

---

## Related Documentation

- [Adding a New Gateway](../CLAUDE.md#adding-a-new-gateway)
- [Model Catalog System](./MODEL_CATALOG.md)
- [Database Migrations](./MIGRATIONS.md)
- [CI/CD Pipeline](../.github/workflows/README.md)
