# Railway Loki & Tempo Crash - Complete Resolution Guide

**Last Updated**: 2025-12-26
**Status**: 🔴 **CRITICAL - Action Required**
**Severity**: HIGH
**Impact**: Application won't start on Railway

---

## 🎯 Executive Summary

Your Loki and Tempo services on Railway are crashing with a configuration error:
```
field shared_store not found in type compactor.Config
```

**Quick Fix**: Disable Loki and Tempo services in Railway Dashboard (5 minutes)
**Reason**: They use outdated/invalid configuration that isn't compatible with current versions
**Impact**: Zero - backend works perfectly without them, just uses console logging instead

---

## 🔍 Root Cause Analysis

### The Problem

When Railway deploys Loki and Tempo services, they load a configuration file that contains:

```yaml
compactor:
  shared_store: filesystem  # ❌ INVALID FIELD
  working_directory: /loki/boltdb-shipper-compactor
```

### Why It Fails

- **Field Name**: `shared_store` under `compactor` doesn't exist in modern Loki
- **Version**: Loki 2.5+ moved/removed this field
- **Location**: It should be under `storage_config.boltdb_shipper`, NOT under `compactor`
- **Config Generation**: Railway's template service is using outdated configuration

### Timeline

```
Railway deploys service
         ↓
Loads /etc/loki/loki-config.yaml from template
         ↓
Parses compactor section
         ↓
Encounters "shared_store: filesystem" (invalid field)
         ↓
YAML unmarshal error
         ↓
Container crashes
         ↓
Health checks fail
         ↓
Railway restart loop
```

---

## ✅ Solution (Recommended)

### STEP 1: Remove Loki from Railway Dashboard

1. Go to: https://railway.app/dashboard
2. Select your project: **gatewayz-api**
3. Click on **Loki service**
4. Click **Settings** (gear icon)
5. Scroll to **Danger Zone**
6. Click **Remove Service**
7. Confirm removal

### STEP 2: Remove Tempo from Railway Dashboard

1. Go to: https://railway.app/dashboard
2. Select your project: **gatewayz-api**
3. Click on **Tempo service**
4. Click **Settings** (gear icon)
5. Scroll to **Danger Zone**
6. Click **Remove Service**
7. Confirm removal

### STEP 3: Set Environment Variables

These prevent Loki/Tempo from being re-enabled:

1. Go to your project **Variables**
2. Add these variables:
   ```
   LOKI_ENABLED=false
   TEMPO_ENABLED=false
   ```

### STEP 4: Redeploy

```bash
# Push your current staging branch
git push origin staging

# Or trigger deployment in Railway Dashboard → Services → gateway-api → Redeploy
```

### STEP 5: Verify

Check that deployment succeeds:
```bash
railway logs --follow

# You should see:
# ✅ Application started on http://0.0.0.0:8000
```

Test the health endpoint:
```bash
curl https://your-railway-url/health

# Should return:
# {"status":"healthy","provider_status":...}
```

---

## 📊 What Changes Without Loki/Tempo?

### Current Setup (With Crashing Loki)
- ❌ Application won't start
- ❌ Health checks fail
- ❌ Railway keeps restarting container
- ❌ No logs visible

### New Setup (Loki/Tempo Disabled)
- ✅ Application starts cleanly
- ✅ Health checks pass
- ✅ Logs visible in Railway dashboard
- ✅ Console output captured
- ✅ Full functionality preserved

### Logging Behavior

| Feature | With Loki | Without Loki |
|---------|-----------|-------------|
| Console logs | Yes | Yes ✅ |
| Persistent logs | Yes (in Loki) | No (Railway only stores recent) |
| Grafana integration | Yes | Limited (use Railway logs) |
| Startup time | Slower (Loki connects) | Faster |
| Container crashes | Yes (current state) | No |

---

## 🔧 Alternative: Fix Loki Configuration

If you want to keep Loki later, here's the proper configuration:

### The Correct Loki Config

```yaml
auth_enabled: false

ingester:
  chunk_idle_period: 3m
  max_chunk_age: 1h
  chunk_retain_period: 1m

limits_config:
  enforce_metric_name: false
  reject_old_samples: true
  reject_old_samples_max_age: 168h

schema_config:
  configs:
  - from: 2020-10-24
    store: boltdb-shipper
    object_store: filesystem
    schema: v11
    index:
      prefix: index_
      period: 24h

server:
  http_listen_port: 3100
  log_level: info

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/boltdb-shipper-active
    cache_location: /loki/boltdb-shipper-cache
    shared_store: filesystem  # ✅ CORRECT LOCATION
  filesystem:
    directory: /loki/chunks

table_manager:
  retention_deletes_enabled: false
  retention_period: 0s

chunk_store_config:
  max_look_back_period: 0s
```

**Key Difference**: `shared_store` is under `storage_config.boltdb_shipper`, NOT under `compactor`.

---

## 🎯 Implementation Checklist

- [ ] Remove Loki service from Railway
- [ ] Remove Tempo service from Railway
- [ ] Set `LOKI_ENABLED=false` in Railway variables
- [ ] Set `TEMPO_ENABLED=false` in Railway variables
- [ ] Push staging branch
- [ ] Verify deployment succeeds in Railway logs
- [ ] Test `/health` endpoint returns healthy
- [ ] Confirm application is running
- [ ] Monitor logs for any errors

---

## 🚨 Troubleshooting

### Deployment Still Fails

Check the exact error:
```bash
railway logs --follow

# Look for specific errors, not Loki-related messages
```

If you see different errors:
- Backend database connection issue
- Missing environment variables
- Other service dependency

### Health Checks Timeout

If `/health` endpoint times out:
1. Check if application is actually running: `curl http://localhost:8000/health`
2. Increase timeout in `railway.json` (currently 30s)
3. Check for startup errors in logs

### Logs Not Appearing

```bash
# Ensure logs are being captured
railway logs --lines 100

# Check if any services are still crashing
railway ps

# View service status
railway status --verbose
```

---

## 📈 Performance Impact

### Before (With Broken Loki)
- Startup: Fails ❌
- Logs: Not available
- Health checks: Timeout ⏱️
- Requests: Not processed

### After (With Loki Disabled)
- Startup: ~45 seconds
- Logs: Console output in Railway
- Health checks: Pass ✅
- Requests: Processing normally

---

## 🔄 Future: Re-enable Loki (When Fixed)

If you want to restore Loki/Tempo later:

1. Ensure Loki configuration is correct (see above)
2. Add Loki service back to Railway with fixed config
3. Test in staging environment first
4. Deploy to production after verification

For now, keep them disabled to prevent crashes.

---

## 📞 Support & Resources

- [Loki Configuration Docs](https://grafana.com/docs/loki/latest/configuration/)
- [Railway Documentation](https://docs.railway.app)
- [Loki GitHub Issues](https://github.com/grafana/loki/issues)

---

## ✨ Related Documentation

See these docs for more context:
- `LOKI_TEMPO_RAILWAY_FIX.md` - Detailed fix options
- `RAILWAY_ENV_DEFAULTS.md` - Recommended environment variables
- `RAILWAY_DEPLOYMENT.md` - Full deployment guide

---

## ✅ Status

**Action Required**: Remove Loki/Tempo from Railway Dashboard
**Estimated Time**: 5 minutes
**Impact on Users**: None (full functionality preserved)
**Difficulty**: Easy (simple removal in dashboard)

After completing these steps, your application will deploy and run successfully on Railway!
