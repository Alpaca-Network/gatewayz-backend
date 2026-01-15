# Provider & Model Sync - Quick Start Guide

## 🎯 TL;DR

```
Providers (code) → Always synced on deploy & startup
Models (APIs)    → Synced every 6 hours automatically
```

---

## ✅ What's Been Set Up

### 1. **Provider Sync** (Always Current with Code)

**Automatic on Every Push:**
```sql
-- Migration: supabase/migrations/20260115000000_sync_providers_from_gateway_registry.sql
-- Syncs all 28 providers from GATEWAY_REGISTRY to database
```

**Automatic on App Startup:**
```python
# src/services/startup.py
# Syncs providers from GATEWAY_REGISTRY every time app starts
```

**Manual Script:**
```bash
python scripts/database/seed_providers.py
```

### 2. **Model Sync** (Fresh from Provider APIs)

**Background Task (Every 6 Hours):**
```python
# Runs automatically in background
# Configurable via MODEL_SYNC_INTERVAL_HOURS env var
```

**GitHub Actions (Every 6 Hours):**
```yaml
# .github/workflows/model-sync.yml
# Scheduled cron job: '0 */6 * * *'
```

**Manual API Endpoints:**
```bash
POST /admin/model-sync/all
POST /admin/model-sync/provider/{provider_slug}
GET  /admin/model-sync/status
```

---

## 🚀 Common Tasks

### Add a New Provider

**Step 1:** Update `GATEWAY_REGISTRY`
```python
# src/routes/catalog.py
GATEWAY_REGISTRY = {
    "my-provider": {
        "name": "My Provider",
        "color": "bg-purple-500",
        "priority": "fast",
        "site_url": "https://myprovider.com"
    },
    # ... rest
}
```

**Step 2:** Push to Git
```bash
git add src/routes/catalog.py
git commit -m "feat: add My Provider"
git push
```

**Done!** Provider syncs automatically via migration + startup.

### Force Immediate Model Sync

```bash
# Sync all providers
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all"

# Sync single provider
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter"

# Check status
curl "https://api.gatewayz.ai/admin/model-sync/status"
```

### Check Sync Status

```bash
# View providers
SELECT COUNT(*) FROM providers;  -- Should be 28

# View models
SELECT COUNT(*) FROM models;     -- Should be 337+

# Recent syncs
SELECT provider_id, COUNT(*) as model_count, MAX(updated_at) as last_sync
FROM models
GROUP BY provider_id;
```

---

## ⚙️ Configuration

### Environment Variables

```bash
# Required (already set in your environment)
SUPABASE_URL=...
SUPABASE_KEY=...

# Model sync config
MODEL_SYNC_INTERVAL_HOURS=6           # Default: 6 hours
SYNC_MODELS_ON_STARTUP=false          # Default: false (faster startup)

# Provider API keys (already configured)
OPENROUTER_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
# ... etc
```

### Adjust Sync Frequency

```bash
# Sync every 3 hours instead of 6
export MODEL_SYNC_INTERVAL_HOURS=3

# Restart application to apply
```

---

## 📊 System Flow

```
┌────────────────────────────────────────────────────────────┐
│                  YOUR WORKFLOW                             │
└────────────────────────────────────────────────────────────┘

1. Edit GATEWAY_REGISTRY
2. git push
3. ✅ Providers auto-sync via migration
4. ✅ App restarts, confirms provider sync
5. ✅ Models auto-sync every 6 hours
6. ✅ Database always up-to-date


┌────────────────────────────────────────────────────────────┐
│                  AUTOMATIC PROCESSES                       │
└────────────────────────────────────────────────────────────┘

Providers:
  ✅ Migration runs on push
  ✅ Startup sync runs on app start

Models:
  ✅ Background task runs every 6h
  ✅ GitHub Actions runs every 6h
  ✅ Manual trigger via API anytime
```

---

## 🔍 Monitoring

### Check Logs

```bash
# Provider sync logs
grep "Synced.*providers" logs/app.log

# Model sync logs
grep "Model sync complete" logs/app.log

# Errors
grep "sync failed" logs/app.log
```

### Health Check

```bash
# Check API status
curl https://api.gatewayz.ai/health

# Check model sync status
curl https://api.gatewayz.ai/admin/model-sync/status
```

### Database Queries

```sql
-- Count providers
SELECT COUNT(*) FROM providers;

-- Count models per provider
SELECT p.name, COUNT(m.id) as model_count
FROM providers p
LEFT JOIN models m ON p.id = m.provider_id
GROUP BY p.id, p.name
ORDER BY model_count DESC;

-- Recent model updates
SELECT p.name, m.model_name, m.updated_at
FROM models m
JOIN providers p ON m.provider_id = p.id
ORDER BY m.updated_at DESC
LIMIT 10;

-- Check for stale models (> 12 hours old)
SELECT p.name, COUNT(m.id) as stale_models
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE m.updated_at < NOW() - INTERVAL '12 hours'
GROUP BY p.name;
```

---

## 🐛 Troubleshooting

### Provider not showing up?

```bash
# Run manual seed
python scripts/database/seed_providers.py

# Check database
SELECT * FROM providers WHERE slug = 'my-provider';
```

### Models outdated?

```bash
# Trigger immediate sync
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all"

# Check last sync
curl "https://api.gatewayz.ai/admin/model-sync/status"
```

### Sync errors?

```bash
# Check provider API keys
echo $OPENROUTER_API_KEY  # Should not be empty

# Test provider API directly
curl https://openrouter.ai/api/v1/models

# Check application logs
grep "ERROR.*sync" logs/app.log
```

---

## 📚 Full Documentation

For detailed information, see:
- **[Complete Guide](docs/PROVIDER_MODEL_SYNC.md)** - Full documentation
- **[Architecture](CLAUDE.md)** - System architecture
- **[Adding Gateways](CLAUDE.md#adding-a-new-gateway)** - Step-by-step guide

---

## ✨ Summary

| What                 | How                          | When                  |
|---------------------|------------------------------|-----------------------|
| **Provider Sync**   | Migration + Startup          | Every push + restart  |
| **Model Sync**      | Background + GitHub Actions  | Every 6 hours         |
| **Manual Trigger**  | API endpoint                 | Anytime               |
| **Configuration**   | Environment variables        | Change & restart      |

**You control**: GATEWAY_REGISTRY (providers)
**System handles**: Model fetching from APIs

**Result**: Always up-to-date database! 🎉
