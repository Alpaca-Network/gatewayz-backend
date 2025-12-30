# Tempo Endpoint Configuration: Staging vs Production

**Issue:** Tempo is currently configured to send traces to the staging instance instead of production.

**Root Cause:** The `TEMPO_OTLP_HTTP_ENDPOINT` environment variable is not properly configured for production, or both staging and production are pointing to the same Tempo instance.

**Date:** 2025-12-30
**Status:** Configuration Fix Required

---

## The Problem

When you have separate staging and production environments, each should send traces to **its own Tempo instance**:

```
Development   → Tempo (Dev)        [http://tempo:4318]
     ↓
Staging       → Tempo (Staging)    [http://tempo.staging.internal:4318]
     ↓
Production    → Tempo (Production) [http://tempo.production.internal:4318]
```

**Currently:** Both staging and production are sending to the staging Tempo instance.

---

## How Tempo Configuration Works

### Configuration Flow

```python
# src/config/config.py (line 280-283)
TEMPO_OTLP_HTTP_ENDPOINT = os.environ.get(
    "TEMPO_OTLP_HTTP_ENDPOINT",
    "http://tempo:4318",  # ← Default (development)
)
```

### How Traces Are Sent

```python
# src/config/opentelemetry_config.py (line 141)
tempo_endpoint = Config.TEMPO_OTLP_HTTP_ENDPOINT

# This endpoint is used to send all traces:
otlp_exporter = OTLPSpanExporter(
    endpoint=f"{tempo_endpoint}/v1/traces",  # ← This is where traces go
    headers={},
)
```

**The endpoint comes from the environment variable, so it must be set correctly per environment.**

---

## Fix: Set Environment-Specific Tempo Endpoints

### Option 1: Railway Deployment (Recommended)

If using Railway for both staging and production:

#### For Staging Environment (Railway Dashboard)

1. Go to **Staging Project** → **Settings** → **Variables**
2. Set or update these variables:

```env
APP_ENV=staging
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.staging.railway.internal:4318
OTEL_SERVICE_NAME=gatewayz-api-staging
```

#### For Production Environment (Railway Dashboard)

1. Go to **Production Project** → **Settings** → **Variables**
2. Set or update these variables:

```env
APP_ENV=production
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.production.railway.internal:4318
OTEL_SERVICE_NAME=gatewayz-api-production
```

**Key Differences:**
- `APP_ENV`: Different values (`staging` vs `production`)
- `TEMPO_OTLP_HTTP_ENDPOINT`: Different Railway internal networks
- `OTEL_SERVICE_NAME`: Different service names (optional but recommended)

---

### Option 2: Grafana Cloud (Alternative)

If you're using Grafana Cloud instead of Railway Tempo:

#### For Staging
```env
APP_ENV=staging
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-prod-xx-staging.grafana.net/tempo
GRAFANA_TEMPO_USERNAME=123456
GRAFANA_TEMPO_API_KEY=glc_staging_key
OTEL_SERVICE_NAME=gatewayz-api-staging
```

#### For Production
```env
APP_ENV=production
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-prod-xx-prod.grafana.net/tempo
GRAFANA_TEMPO_USERNAME=123456
GRAFANA_TEMPO_API_KEY=glc_production_key
OTEL_SERVICE_NAME=gatewayz-api-production
```

---

### Option 3: Self-Hosted Tempo Instances

If you have separate Tempo instances running:

#### For Staging
```env
APP_ENV=staging
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-staging.yourdomain.com
OTEL_SERVICE_NAME=gatewayz-api-staging
```

#### For Production
```env
APP_ENV=production
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-production.yourdomain.com
OTEL_SERVICE_NAME=gatewayz-api-production
```

---

## How to Verify the Fix

### 1. Check Current Configuration

```bash
# On Railway staging project shell:
echo "Staging Tempo Endpoint: $TEMPO_OTLP_HTTP_ENDPOINT"
echo "Staging APP_ENV: $APP_ENV"

# On Railway production project shell:
echo "Production Tempo Endpoint: $TEMPO_OTLP_HTTP_ENDPOINT"
echo "Production APP_ENV: $APP_ENV"
```

### 2. Check Application Logs

When the application starts, it logs the Tempo endpoint:

```
🔭 Initializing OpenTelemetry tracing...
   Tempo endpoint: http://tempo.staging.railway.internal:4318  ← Should be different per env
✅ OpenTelemetry tracing initialized successfully
```

### 3. Query Traces by Environment Tag

In Grafana (Explore → Tempo), you can filter traces by environment:

```
{service.name="gatewayz-api-staging"}   ← Should show staging traces only
{service.name="gatewayz-api-production"} ← Should show production traces only
```

### 4. Verify in Tempo UI

Access Tempo UI and look for service names:
- **Staging traces**: Service name = `gatewayz-api-staging`
- **Production traces**: Service name = `gatewayz-api-production`

If you only see one service name in Tempo, then both environments are using the same endpoint.

---

## Step-by-Step Fix (Railway)

### Step 1: Check Current Staging Configuration

```bash
# Login to Railway
railway login

# Select staging project
railway link [staging-project-id]

# Check variables
railway variables
```

**Look for:**
- `TEMPO_OTLP_HTTP_ENDPOINT` (should be staging instance)
- `APP_ENV=staging`
- `OTEL_SERVICE_NAME=gatewayz-api-staging`

### Step 2: Set Staging Tempo Endpoint

```bash
railway variables set TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.staging.railway.internal:4318
railway variables set OTEL_SERVICE_NAME=gatewayz-api-staging
railway variables set APP_ENV=staging
```

### Step 3: Check Current Production Configuration

```bash
# Select production project
railway link [production-project-id]

# Check variables
railway variables
```

### Step 4: Set Production Tempo Endpoint

```bash
railway variables set TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.production.railway.internal:4318
railway variables set OTEL_SERVICE_NAME=gatewayz-api-production
railway variables set APP_ENV=production
```

### Step 5: Restart Both Services

```bash
# Redeploy staging
railway up --staging

# Redeploy production
railway up --production
```

### Step 6: Verify in Logs

Check the application startup logs:

**Staging logs should show:**
```
🔭 Initializing OpenTelemetry tracing...
   Tempo endpoint: http://tempo.staging.railway.internal:4318
✅ OpenTelemetry tracing initialized successfully
```

**Production logs should show:**
```
🔭 Initializing OpenTelemetry tracing...
   Tempo endpoint: http://tempo.production.railway.internal:4318
✅ OpenTelemetry tracing initialized successfully
```

---

## Complete Environment Variable Reference

### Staging Environment Variables

```env
# Core
APP_ENV=staging
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.staging.railway.internal:4318
TEMPO_OTLP_GRPC_ENDPOINT=tempo.staging.railway.internal:4317
OTEL_SERVICE_NAME=gatewayz-api-staging

# Database
SUPABASE_URL=https://staging-proj.supabase.co
SUPABASE_KEY=staging-key

# Error Tracking (Sentry)
SENTRY_DSN=https://key@sentry.io/staging-project-id
SENTRY_ENVIRONMENT=staging
SENTRY_RELEASE=2.0.3
SENTRY_TRACES_SAMPLE_RATE=1.0  # Full tracing in staging
```

### Production Environment Variables

```env
# Core
APP_ENV=production
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.production.railway.internal:4318
TEMPO_OTLP_GRPC_ENDPOINT=tempo.production.railway.internal:4317
OTEL_SERVICE_NAME=gatewayz-api-production

# Database
SUPABASE_URL=https://production-proj.supabase.co
SUPABASE_KEY=production-key

# Error Tracking (Sentry)
SENTRY_DSN=https://key@sentry.io/production-project-id
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=2.0.3
SENTRY_TRACES_SAMPLE_RATE=0.1  # 10% sampling in production (reduce load)
```

---

## Troubleshooting

### Symptom 1: Production Traces Showing in Staging Tempo

**Cause:** Both environments have the same `TEMPO_OTLP_HTTP_ENDPOINT`

**Fix:**
```bash
# Check production config
railway link [production-project-id]
railway variables | grep TEMPO_OTLP_HTTP_ENDPOINT

# Should show production URL, not staging URL
# If it shows staging URL, update it:
railway variables set TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.production.railway.internal:4318
```

### Symptom 2: "Tempo endpoint is not reachable" Error

**Cause:** The Tempo instance is down or the endpoint URL is wrong

**Fix:**
```bash
# 1. Verify the endpoint URL
railway variables | grep TEMPO

# 2. Test connectivity from the container
# SSH into the Railway container and test:
curl -v http://tempo.production.railway.internal:4318/metrics
```

### Symptom 3: No Traces Showing in Tempo

**Cause:** Either:
- `TEMPO_ENABLED=false` (traces disabled)
- `TEMPO_OTLP_HTTP_ENDPOINT` is wrong
- Tempo service is down

**Fix:**
```bash
# 1. Check if Tempo is enabled
railway variables | grep TEMPO_ENABLED
# Should be: TEMPO_ENABLED=true

# 2. Check the endpoint
railway variables | grep TEMPO_OTLP_HTTP_ENDPOINT

# 3. Check logs
railway logs
# Look for: "OpenTelemetry tracing initialized successfully"
# If you see "Skipping OpenTelemetry initialization", check why
```

---

## Why This Matters

### For Monitoring
- **Staging traces** should be isolated from **production traces**
- You need to see which environment had an issue
- Different trace retention policies per environment

### For Compliance
- Production data stays in production
- Staging can be purged without affecting production
- Audit trails remain separate

### For Debugging
- Filter by environment when investigating incidents
- Staging issues won't contaminate production metrics
- Clear visibility into what's running where

---

## Code References

**Related Code:**
- Configuration: `src/config/config.py` (lines 274-287)
- OpenTelemetry Setup: `src/config/opentelemetry_config.py` (lines 141-142)
- Initialization: `src/main.py` (startup event)

**Environment Variables:**
- `TEMPO_ENABLED` - Enable/disable tracing
- `TEMPO_OTLP_HTTP_ENDPOINT` - Where to send traces
- `OTEL_SERVICE_NAME` - Service identifier in traces
- `APP_ENV` - Environment identifier (staging vs production)
- `SENTRY_ENVIRONMENT` - Must match `APP_ENV` for consistency

---

## Summary

| Aspect | Staging | Production |
|--------|---------|-----------|
| `APP_ENV` | `staging` | `production` |
| `TEMPO_ENABLED` | `true` | `true` |
| `TEMPO_OTLP_HTTP_ENDPOINT` | `http://tempo.staging.railway.internal:4318` | `http://tempo.production.railway.internal:4318` |
| `OTEL_SERVICE_NAME` | `gatewayz-api-staging` | `gatewayz-api-production` |
| `SENTRY_ENVIRONMENT` | `staging` | `production` |
| Trace Sampling | 100% | 10-50% |
| Trace Retention | 7-30 days | 30-90 days |
| Data Isolation | ✅ Separate | ✅ Separate |

**Action Required:**
1. ✅ Update production `TEMPO_OTLP_HTTP_ENDPOINT` to production Tempo instance
2. ✅ Update `APP_ENV=production` in production variables
3. ✅ Update `OTEL_SERVICE_NAME=gatewayz-api-production` in production
4. ✅ Redeploy both staging and production
5. ✅ Verify in logs and Tempo UI

