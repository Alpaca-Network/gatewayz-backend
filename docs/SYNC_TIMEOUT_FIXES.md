# Sync Timeout Fixes & Best Practices

## Problem

Sync endpoints timeout because:
- **Full sync takes 5-30 minutes** (fetching from 30+ providers)
- **Railway timeout is 4 minutes** (now increased to 30 minutes)
- **HTTP clients have default timeouts** (30-120 seconds)
- **Synchronous execution blocks** the HTTP request

## Solutions Overview

| Solution | Speed | Timeout Risk | Use Case |
|----------|-------|--------------|----------|
| Background Tasks | Instant | ✅ None | Production, cron jobs |
| Provider-Specific | 10-60s | ⚠️ Low | Testing, debugging |
| Increased Timeout | N/A | ⚠️ Medium | Manual testing |
| Dry-Run First | 5-30s | ✅ None | Testing changes |

---

## Solution 1: Use Background Tasks ⭐ RECOMMENDED

### Why This Works
- Returns **immediately** (< 1 second)
- Sync runs **asynchronously** in background
- **No timeout possible** - the HTTP request completes right away
- Built into the endpoints already

### Manual Testing

```bash
# Set environment
export STAGING_URL="https://gatewayz-staging.up.railway.app"
export ADMIN_KEY="your-admin-key"

# Full sync in background (returns immediately)
curl -X POST "$STAGING_URL/admin/model-sync/full?background=true"

# Response (instant):
# {
#   "status": "queued",
#   "message": "Full sync queued for background execution"
# }

# Check status later
curl -s "$STAGING_URL/admin/model-sync/status" | jq .

# Pricing sync in background
curl -X POST "$STAGING_URL/pricing/sync/run?background=true"
```

### Automated Cron Jobs

**Already updated in `railway.toml`:**

```toml
[[crons]]
schedule = "0 */6 * * *"
command = "curl -X POST 'http://localhost:$PORT/admin/model-sync/full?background=true'"
```

### Supported Endpoints

All these support `?background=true`:

- `POST /admin/model-sync/full?background=true`
- `POST /admin/model-sync/provider/{slug}?background=true`
- `POST /pricing/sync/run?background=true`
- `POST /pricing/sync/run/{provider}?background=true`

---

## Solution 2: Sync Providers One at a Time

### Why This Works
- Each provider sync: **10-60 seconds** (rarely times out)
- You control which providers to sync
- Good for **debugging specific providers**

### Example Script

```bash
#!/bin/bash
# Sync important providers sequentially

PROVIDERS=("openrouter" "deepinfra" "groq" "fireworks" "cerebras")

for provider in "${PROVIDERS[@]}"; do
    echo "Syncing $provider..."

    # Option 1: Foreground (wait for completion)
    curl -X POST "$STAGING_URL/admin/model-sync/provider/$provider"

    # Option 2: Background (no wait)
    # curl -X POST "$STAGING_URL/admin/model-sync/provider/$provider?background=true"

    echo "✓ $provider done"
    sleep 2
done

echo "All providers synced!"
```

### Available Providers

Get the full list:

```bash
curl -s "$STAGING_URL/admin/model-sync/providers" | jq -r '.providers[]'
```

---

## Solution 3: Increase Timeout Limits

### Railway Config

**Already increased in `railway.json`:**

```json
{
  "requestTimeout": 1800  // 30 minutes (was 240 = 4 minutes)
}
```

### Client Timeout

When using curl, increase timeout:

```bash
# Default: 30 seconds timeout
curl -X POST "$STAGING_URL/admin/model-sync/full"

# Increased: 30 minutes timeout
curl -X POST "$STAGING_URL/admin/model-sync/full" --max-time 1800
```

### Railway Deployment

After changing `railway.json`, deploy:

```bash
railway up
# or
git add railway.json railway.toml
git commit -m "fix: increase sync timeouts"
git push
```

---

## Solution 4: Use Dry-Run First

### Why Dry-Run
- **Fast** (5-30 seconds)
- **Safe** (no database changes)
- Shows what **would** change
- Test without risk of timeout

### Examples

```bash
# Dry-run full sync (fast, safe)
curl -X POST "$STAGING_URL/admin/model-sync/full?dry_run=true" | jq .

# Sample response:
# {
#   "success": true,
#   "dry_run": true,
#   "providers": {...},
#   "models": {
#     "total_models_synced": 18432,  # What WOULD be synced
#     "providers_processed": 30
#   }
# }

# Dry-run specific provider
curl -X POST "$STAGING_URL/admin/model-sync/provider/openrouter?dry_run=true" | jq .

# Dry-run pricing sync
curl -X POST "$STAGING_URL/pricing/sync/dry-run" | jq .
```

---

## Recommended Workflow

### First Time Setup

```bash
# 1. Check current status (fast)
curl -s "$STAGING_URL/admin/model-sync/status" | jq .

# 2. Dry-run to see what will change (safe, fast)
curl -X POST "$STAGING_URL/admin/model-sync/full?dry_run=true" | jq .

# 3. If looks good, run in background (no timeout)
curl -X POST "$STAGING_URL/admin/model-sync/full?background=true"

# 4. Wait a few minutes, then check status
curl -s "$STAGING_URL/admin/model-sync/status" | jq .

# 5. Verify models were synced
curl -s "$STAGING_URL/catalog/models-db/stats" | jq .
```

### Regular Maintenance

```bash
# Option 1: Let cron jobs handle it (automated)
# Already configured in railway.toml - every 6 hours

# Option 2: Manual trigger in background
curl -X POST "$STAGING_URL/admin/model-sync/full?background=true"
```

### Debugging Specific Provider

```bash
# 1. Check which providers have issues
curl -s "$STAGING_URL/providers/health/down" | jq .

# 2. Dry-run specific provider
curl -X POST "$STAGING_URL/admin/model-sync/provider/openrouter?dry_run=true" | jq .

# 3. If looks good, sync it
curl -X POST "$STAGING_URL/admin/model-sync/provider/openrouter" | jq .
```

---

## Interactive Testing Script

We created a helper script for you:

```bash
# Run interactive test menu
./scripts/test_sync_endpoints.sh

# Sample menu:
# 1) Check Model Sync Status (Quick)
# 2) List Available Providers (Quick)
# 3) Dry-Run: Single Provider Sync (Safe)
# 4) Actual: Single Provider Sync (OpenRouter)
# 5) Background: Full Sync (No Timeout) ⭐
# 6) Foreground: Full Sync (May Timeout)
# 7) Check Pricing Sync Status (Quick)
# 8) Pricing Dry-Run (Safe)
# 9) Background: Pricing Sync (No Timeout) ⭐
# ...
```

---

## Quick Reference Commands

### No Timeout Risk ✅

```bash
# These are safe and won't timeout:
curl -X POST "$STAGING_URL/admin/model-sync/full?background=true"
curl -X POST "$STAGING_URL/pricing/sync/run?background=true"
curl -X POST "$STAGING_URL/admin/model-sync/full?dry_run=true"
curl -s "$STAGING_URL/admin/model-sync/status"
```

### May Timeout ⚠️

```bash
# These MAY timeout if provider is slow:
curl -X POST "$STAGING_URL/admin/model-sync/full"
curl -X POST "$STAGING_URL/pricing/sync/run"
```

### Slow but Usually OK 🐢

```bash
# Single provider syncs (10-60s each):
curl -X POST "$STAGING_URL/admin/model-sync/provider/openrouter"
curl -X POST "$STAGING_URL/admin/model-sync/provider/deepinfra"
curl -X POST "$STAGING_URL/pricing/sync/run/openrouter"
```

---

## Monitoring Background Tasks

### Check if Sync is Running

```bash
# Get latest sync status
curl -s "$STAGING_URL/admin/model-sync/status" | jq .

# Sample response:
{
  "providers": {
    "in_database": 30,
    "with_fetch_functions": 30
  },
  "models": {
    "stats": {
      "total": 18432,
      "active": 18200,
      "by_provider": {...}
    }
  }
}
```

### Check Pricing Sync

```bash
# Current status
curl -s "$STAGING_URL/pricing/sync/status" | jq .

# Recent history
curl -s "$STAGING_URL/pricing/sync/history?limit=10" | jq .
```

### Prometheus Metrics

```bash
# Check sync metrics
curl -s "$STAGING_URL/metrics" | grep -i "sync\|pricing"
```

---

## Troubleshooting

### Still Getting Timeouts?

1. **Use background parameter**: `?background=true`
2. **Check Railway logs**: `railway logs`
3. **Verify timeout increased**: Check `railway.json` deployed
4. **Test single provider**: Isolate slow providers
5. **Check network**: Test from different location

### How to Know if Background Sync Completed?

```bash
# Check model count before
BEFORE=$(curl -s "$STAGING_URL/catalog/models-db/stats" | jq -r '.total')

# Trigger background sync
curl -X POST "$STAGING_URL/admin/model-sync/full?background=true"

# Wait 5-10 minutes, then check again
sleep 600
AFTER=$(curl -s "$STAGING_URL/catalog/models-db/stats" | jq -r '.total')

echo "Models before: $BEFORE"
echo "Models after: $AFTER"
echo "New models: $((AFTER - BEFORE))"
```

### Provider Taking Too Long?

```bash
# Skip slow providers
curl -X POST "$STAGING_URL/admin/model-sync/all?providers=openrouter,deepinfra,groq"

# Or sync fast ones first
FAST_PROVIDERS=("openrouter" "deepinfra" "groq" "fireworks")
for p in "${FAST_PROVIDERS[@]}"; do
    curl -X POST "$STAGING_URL/admin/model-sync/provider/$p?background=true"
done
```

---

## Summary

### ✅ DO THIS

- ✅ Use `?background=true` for all production syncs
- ✅ Use `?dry_run=true` before actual syncs
- ✅ Let cron jobs handle automated syncs
- ✅ Monitor with `/status` endpoints
- ✅ Test with interactive script

### ❌ DON'T DO THIS

- ❌ Run full sync without `background=true`
- ❌ Skip dry-run in production
- ❌ Sync all providers synchronously
- ❌ Ignore timeout errors (use background instead)

---

## Links

- **Railway Docs**: https://docs.railway.app/reference/config-as-code
- **Background Tasks**: `src/routes/model_sync.py:254`
- **Pricing Sync**: `src/routes/pricing_sync.py:87`
- **Test Script**: `scripts/test_sync_endpoints.sh`
- **E2E Tests**: `scripts/e2e_pricing_sync_test_v2.sh`
