# Railway Environment Variables - Recommended Defaults

## Quick Copy-Paste for Railway Dashboard

Go to your Railway project → Variables → Add these for stable deployment:

```
# Disable Loki/Tempo (these are causing crashes)
LOKI_ENABLED=false
TEMPO_ENABLED=false

# Application Configuration
APP_ENV=production
LOG_LEVEL=INFO

# FastAPI Settings
WORKERS=2
TIMEOUT=60

# Health Checks
HEALTHCHECK_INTERVAL=30
HEALTHCHECK_TIMEOUT=30
```

## Why These Defaults?

| Variable | Value | Reason |
|----------|-------|--------|
| `LOKI_ENABLED` | `false` | Loki config incompatibility on Railway - causes crashes |
| `TEMPO_ENABLED` | `false` | Tempo config incompatibility on Railway - causes crashes |
| `APP_ENV` | `production` | Ensure production-level error handling |
| `LOG_LEVEL` | `INFO` | Balanced logging - not too verbose, not too quiet |
| `WORKERS` | `2` | Reasonable default for most workloads (adjust based on traffic) |
| `TIMEOUT` | `60` | Prevent hanging requests (adjust for long-running tasks) |

## How to Set in Railway Dashboard

1. Go to https://railway.app/dashboard
2. Select your project (gatewayz-api)
3. Click on "Variables" tab
4. Click "Add Variable"
5. Paste each variable name and value
6. Click "Deploy" to apply changes

## How to Set via Railway CLI

```bash
# Install Railway CLI if needed
npm i -g @railway/cli
railway login

# Set variables
railway variables set LOKI_ENABLED false
railway variables set TEMPO_ENABLED false
railway variables set APP_ENV production
railway variables set LOG_LEVEL INFO
railway variables set WORKERS 2
railway variables set TIMEOUT 60

# Verify
railway variables

# Deploy
railway up
```

## Monitoring Variables

After deployment, you can check values:

```bash
# View all variables
railway variables

# Check specific variable
railway variables get LOKI_ENABLED

# View in Railway dashboard
# Project → Variables → Scroll to see all
```

## What to Monitor

After setting these variables, check:

1. **Deployment Status**: Should complete successfully
   ```bash
   railway logs --follow
   ```

2. **Health Endpoint**: Should return healthy
   ```bash
   curl https://your-railway-url/health
   ```

3. **Application Logs**: Should show no crashes
   ```bash
   railway logs --lines 50 | grep -i error
   ```

## Troubleshooting

### If deployment still fails:
```bash
# Check detailed logs
railway logs --follow

# Reset all variables
railway variables unset LOKI_ENABLED
railway variables unset TEMPO_ENABLED

# Or use Railway dashboard to remove them manually
```

### If health checks timeout:
```bash
# Increase initial delay for health checks
# (This is configured in railway.json)
# Check railay.json under gateway-api -> deploy -> healthchecks
```

## Performance Tuning (Optional)

After deployment is stable, you can tune:

```bash
# For high traffic
railway variables set WORKERS 4

# For faster startup (reduce health check delay)
# Edit railway.json: deployment.healthchecks.initialDelay = 30

# For memory-constrained environments
railway variables set WORKERS 1
```

---

**Last Updated**: 2025-12-26
**Status**: Recommended for production deployment on Railway
