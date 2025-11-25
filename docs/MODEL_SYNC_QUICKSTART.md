# Model Sync Quick Start Guide

## 5-Minute Setup

### 1. Test the Sync (Dry Run)

First, test without writing to the database:

```bash
# From project root
cd /path/to/gatewayz-backend

# Test sync for one provider
python scripts/sync_models.py --providers openrouter --dry-run
```

**Expected Output:**
```
=============================================================
Model Catalog Sync Script
=============================================================
Providers: openrouter
Dry Run: True
Verbose: False
=============================================================

2025-11-25 10:00:00 - INFO - Starting model sync for 1 providers...
2025-11-25 10:00:01 - INFO - Fetching models from openrouter...
2025-11-25 10:00:02 - INFO - Fetched 150 models from openrouter
2025-11-25 10:00:03 - INFO - Transformed 148 models for openrouter (2 skipped)
2025-11-25 10:00:03 - INFO - DRY RUN: Would sync 148 models for openrouter

=============================================================
Sync Complete
=============================================================
Success: True
Providers Processed: 1
Models Fetched: 150
Models Transformed: 148
Models Skipped: 2
Models Synced: 0  # 0 because dry run
```

### 2. Actually Sync Models

Once satisfied with dry run, sync for real:

```bash
# Sync OpenRouter models
python scripts/sync_models.py --providers openrouter
```

**Expected Output:**
```
...
2025-11-25 10:05:03 - INFO - Syncing 148 models to database...
2025-11-25 10:05:04 - INFO - Successfully synced 148 models for openrouter

=============================================================
Sync Complete
=============================================================
Success: True
Providers Processed: 1
Models Fetched: 150
Models Transformed: 148
Models Skipped: 2
Models Synced: 148  # Actually synced!
```

### 3. Verify Models in Database

Check your database to see the models:

```bash
# Using API
curl http://localhost:8000/models/stats

# Or query database directly
# SELECT COUNT(*) FROM models WHERE provider_id = (SELECT id FROM providers WHERE slug = 'openrouter');
```

**Expected Response:**
```json
{
  "total": 148,
  "active": 148,
  "by_modality": {
    "text->text": 140,
    "text->image": 5,
    "multimodal": 3
  }
}
```

### 4. Sync All Providers

Once comfortable with one provider, sync all:

```bash
# Dry run first
python scripts/sync_models.py --dry-run

# Then actually sync
python scripts/sync_models.py
```

**Expected Output:**
```
=============================================================
Sync Complete
=============================================================
Success: True
Providers Processed: 21
Models Fetched: 5432
Models Transformed: 5380
Models Skipped: 52
Models Synced: 5380

Per-Provider Summary
=============================================================
✓ openrouter          | Fetched:  150 | Transformed:  148 | Skipped:    2 | Synced:  148
✓ deepinfra           | Fetched:  245 | Transformed:  243 | Skipped:    2 | Synced:  243
✓ featherless         | Fetched: 6452 | Transformed: 6400 | Skipped:   52 | Synced: 6400
✗ provider-x          | Error: API key not configured
...
```

## Common Use Cases

### Use Case 1: Daily Automated Sync

Set up a daily cron job:

```bash
# Add to crontab (crontab -e)
0 2 * * * cd /path/to/gatewayz-backend && python scripts/sync_models.py >> /var/log/model-sync.log 2>&1
```

### Use Case 2: Sync After Adding New Provider

When you add a new provider to your system:

```bash
# 1. Test fetch for new provider
python scripts/sync_models.py --providers new-provider --dry-run

# 2. Actually sync
python scripts/sync_models.py --providers new-provider

# 3. Verify
curl http://localhost:8000/models?provider_slug=new-provider
```

### Use Case 3: Emergency Model Catalog Update

When a provider releases important new models:

```bash
# Quick sync for specific provider
python scripts/sync_models.py --providers openrouter

# Verify new models
curl http://localhost:8000/models?provider_slug=openrouter | grep "gpt-5"
```

### Use Case 4: Debugging Sync Issues

When troubleshooting:

```bash
# Verbose mode + single provider + dry run
python scripts/sync_models.py --providers openrouter --dry-run --verbose 2>&1 | tee sync-debug.log

# Review logs
less sync-debug.log
```

## Using the API Instead of CLI

### Test Sync via API

```bash
# Get list of available providers
curl http://localhost:8000/admin/model-sync/providers

# Dry run sync for one provider
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter?dry_run=true"

# Actually sync
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter"
```

### Sync All via API

```bash
# Sync all providers
curl -X POST "http://localhost:8000/admin/model-sync/all"

# Sync specific providers
curl -X POST "http://localhost:8000/admin/model-sync/all?providers=openrouter&providers=deepinfra"
```

### Check Sync Status

```bash
curl http://localhost:8000/admin/model-sync/status
```

## Verification Checklist

After syncing, verify:

- [ ] Providers created in database
  ```bash
  curl http://localhost:8000/providers
  ```

- [ ] Models created for each provider
  ```bash
  curl http://localhost:8000/models/stats
  ```

- [ ] Models have correct data
  ```bash
  curl "http://localhost:8000/models?provider_slug=openrouter&limit=5"
  ```

- [ ] No duplicate models
  ```bash
  # Check in database:
  # SELECT provider_model_id, COUNT(*)
  # FROM models
  # GROUP BY provider_id, provider_model_id
  # HAVING COUNT(*) > 1;
  ```

- [ ] Pricing data populated
  ```bash
  curl http://localhost:8000/models?limit=5 | grep "pricing"
  ```

## Troubleshooting Quick Fixes

### Issue: "Provider not found"

```bash
# List available providers
python scripts/sync_models.py --help
# or
curl http://localhost:8000/admin/model-sync/providers
```

### Issue: "No models fetched"

```bash
# Check API key is set
echo $OPENROUTER_API_KEY

# Set if missing
export OPENROUTER_API_KEY="sk-or-v1-..."

# Try again
python scripts/sync_models.py --providers openrouter --verbose
```

### Issue: "Database connection error"

```bash
# Check environment variables
echo $SUPABASE_URL
echo $SUPABASE_KEY

# Set if missing (from .env file)
export SUPABASE_URL="https://..."
export SUPABASE_KEY="eyJ..."
```

### Issue: Many models skipped

```bash
# Run with verbose to see why
python scripts/sync_models.py --providers openrouter --verbose 2>&1 | grep "Skipping"

# Common reasons:
# - Missing model ID
# - Invalid data format
# - Deprecated models
```

## Next Steps

1. **Schedule Regular Syncs**: Set up cron job or systemd timer
2. **Monitor Performance**: Track sync times and model counts
3. **Add More Providers**: Extend to all 20+ supported providers
4. **Integrate with CI/CD**: Add to deployment pipeline
5. **Set Up Alerts**: Get notified when sync fails

## Pro Tips

1. **Start Small**: Test with 1-2 providers before syncing all
2. **Use Dry Run**: Always test with `--dry-run` first
3. **Check Logs**: Review logs for warnings and errors
4. **Verify Data**: Spot-check a few models in the database
5. **Schedule Wisely**: Run during low-traffic hours (2-4 AM)
6. **Monitor API Limits**: Some providers have rate limits
7. **Keep Keys Secure**: Use environment variables, never commit keys

## Support

- **Documentation**: See [MODEL_SYNC.md](./MODEL_SYNC.md) for full details
- **Issues**: Check logs with `--verbose` flag
- **Database Schema**: See providers and models table documentation
