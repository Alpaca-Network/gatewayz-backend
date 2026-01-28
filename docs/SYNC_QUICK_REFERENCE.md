# Model & Pricing Sync - Quick Reference

## 🚀 Quick Start

### Status Check
```bash
# Check if automatic sync is running
curl https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}/scheduler/status

# Check last sync
curl https://api.gatewayz.ai/pricing/sync/history?limit=1
```

### Manual Sync (Most Common)

```bash
# Sync all models from all providers
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# Sync specific provider
curl -X POST https://api.gatewayz.ai/admin/model-sync/provider/openrouter

# Sync pricing (background job - recommended)
curl -X POST https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}
```

---

## ⚙️ Configuration

### Environment Variables (.env)

```bash
# Automatic Pricing Sync
PRICING_SYNC_ENABLED=true                    # Enable/disable
PRICING_SYNC_INTERVAL_HOURS=6                # Sync every 6 hours
PRICING_SYNC_PROVIDERS=openrouter,featherless,nearai,alibaba-cloud

# Database
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your-service-role-key
```

### Change Sync Frequency

```bash
# Option 1: Environment variable (requires restart)
export PRICING_SYNC_INTERVAL_HOURS=2

# Option 2: Railway/Vercel dashboard
# Add/edit: PRICING_SYNC_INTERVAL_HOURS=2
```

---

## 📝 Common Commands

### Model Sync

```bash
# List available providers
curl https://api.gatewayz.ai/admin/model-sync/providers

# Test sync without changes (dry-run)
curl -X POST "https://api.gatewayz.ai/admin/model-sync/provider/openrouter?dry_run=true"

# Sync all models
curl -X POST https://api.gatewayz.ai/admin/model-sync/all

# Sync specific providers
curl -X POST "https://api.gatewayz.ai/admin/model-sync/all?providers=openrouter&providers=deepinfra"

# Check sync status
curl https://api.gatewayz.ai/admin/model-sync/status
```

### Pricing Sync

```bash
# Dry-run (preview changes)
curl -X POST https://api.gatewayz.ai/pricing/sync/dry-run

# Execute sync (background)
curl -X POST "https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}"

# Check job status
curl "https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}/status/{JOB_ID}"

# View history
curl "https://api.gatewayz.ai/pricing/sync/history?limit=10"

# Get scheduler status
curl "https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}/scheduler/status"
```

### Python Scripts

```bash
# Sync models
python scripts/sync_models.py

# Sync pricing
python scripts/manual_sync_now.py

# Check status
python scripts/check_sync_status.py

# Clear stuck syncs
python scripts/clear_stuck_sync.py
```

---

## 🔍 Monitoring

### Database Queries

```sql
-- Recent sync jobs
SELECT * FROM pricing_sync_jobs
ORDER BY triggered_at DESC
LIMIT 10;

-- Active syncs
SELECT * FROM pricing_sync_jobs
WHERE status IN ('queued', 'running');

-- Sync history
SELECT * FROM pricing_sync_log
ORDER BY sync_started_at DESC
LIMIT 20;

-- Models by provider
SELECT provider_id, COUNT(*) as model_count
FROM models
WHERE is_active = true
GROUP BY provider_id;

-- Recent pricing changes
SELECT * FROM model_pricing_history
ORDER BY changed_at DESC
LIMIT 50;
```

### Check Logs

```bash
# Application logs
tail -f logs/app.log | grep "pricing sync"

# Pricing sync specific logs
tail -f logs/pricing_sync.log

# Railway/Vercel
railway logs --filter="pricing sync"
vercel logs | grep "pricing sync"
```

### Prometheus Metrics

```bash
# View metrics
curl https://api.gatewayz.ai/metrics | grep pricing

# Key metrics:
# - pricing_scheduled_sync_runs_total
# - pricing_scheduled_sync_duration_seconds
# - pricing_last_sync_timestamp
# - pricing_models_synced_total
```

---

## 🚨 Troubleshooting

### Stale Frontend Data
```python
# Clear caches
from src.cache import clear_models_cache
from src.services.model_catalog_cache import invalidate_full_catalog

clear_models_cache()
invalidate_full_catalog()
```

### Stuck Syncs
```bash
# Clear stuck syncs
python scripts/clear_stuck_sync.py

# Or via API
curl -X POST https://api.gatewayz.ai/admin/pricing/sync/cleanup
```

### Database Connection Issues
```bash
# Test connection
python -c "from src.config.supabase_config import get_supabase_client; get_supabase_client(); print('✅ OK')"

# Check environment
echo $SUPABASE_URL
echo $SUPABASE_KEY
```

---

## 📊 Understanding Sync Status

### Job Status Values
- `queued` - Sync job created, waiting to start
- `running` - Sync currently in progress
- `completed` - Sync finished successfully
- `failed` - Sync encountered errors

### Pricing Source Values
- `database` - From database (Phase 2)
- `manual` - From manual_pricing.json
- `cross-reference` - From OpenRouter cross-reference
- `provider` - Direct from provider API
- `default` - Fallback default pricing

---

## 🎯 Best Practices

1. ✅ **Let automatic sync handle routine updates** (every 6 hours)
2. ✅ **Use dry-run before production syncs**
3. ✅ **Use background sync for API calls** (prevents timeouts)
4. ✅ **Monitor sync metrics in Prometheus/Grafana**
5. ✅ **Check sync history after manual syncs**
6. ❌ **Don't sync too frequently** (respect provider rate limits)
7. ❌ **Don't sync multiple providers simultaneously** (unless necessary)

---

## 📱 One-Liner Commands

```bash
# Complete sync workflow
curl -X POST https://api.gatewayz.ai/admin/model-sync/all && \
curl -X POST https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}

# Check everything is working
curl https://api.gatewayz.ai/admin/model-sync/status && \
curl https://api.gatewayz.ai/admin/pricing/sync/{ADMIN_KEY}/scheduler/status

# Emergency full refresh
python scripts/sync_models.py && python scripts/manual_sync_now.py
```

---

## 🔗 Related Documentation

- Full Guide: `docs/MODEL_SYNC_GUIDE.md`
- Architecture: `docs/architecture.md`
- Database Schema: `supabase/migrations/`
- Codebase Context: `CLAUDE.md`

---

**Quick Help**:
- Models in DB but not showing? → Clear cache
- Sync taking too long? → Use background mode
- Pricing outdated? → Check last sync timestamp
- Sync failing? → Check provider API keys in environment

**Last Updated**: 2026-01-27
