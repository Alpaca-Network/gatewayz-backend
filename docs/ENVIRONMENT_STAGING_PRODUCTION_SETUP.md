# Staging & Production Environment Setup Guide

**Last Updated:** 2025-12-30
**Version:** 2.0.3
**Status:** ✅ Complete Reference

---

## Overview

This guide covers **all required and optional environment variables** for configuring staging and production environments. The application uses the same configuration variables but with **different values** for each environment.

### Key Points
- ✅ **No application-level `staging_api_key` or `production_api_key` variables** exist
- ✅ Environment is controlled via `APP_ENV` variable (`staging` or `production`)
- ✅ All API keys use the **same variable names** but different values per environment
- ✅ Different Supabase projects can be used for staging vs production
- ✅ Environment-specific behavior is controlled via `APP_ENV` setting

---

## Environment Detection

### APP_ENV Variable (Required)
```env
# Staging Environment
APP_ENV=staging

# Production Environment
APP_ENV=production

# Development (local)
APP_ENV=development
```

**How it's used in code** (`src/config/config.py`):
```python
IS_PRODUCTION = APP_ENV == "production"
IS_STAGING = APP_ENV == "staging"
IS_DEVELOPMENT = APP_ENV == "development"
IS_TESTING = APP_ENV in {"testing", "test"} or os.environ.get("TESTING") == "true"
```

---

## Required Environment Variables

### 1. Database Configuration (REQUIRED - Different for Each Environment)

#### Staging Database
```env
# Use staging Supabase project
SUPABASE_URL=https://staging-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... (staging anon key)
SUPABASE_DB_DSN=postgresql://postgres:password@db.supabase.co:5432/postgres (optional)
```

#### Production Database
```env
# Use production Supabase project
SUPABASE_URL=https://production-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... (production anon key)
SUPABASE_DB_DSN=postgresql://postgres:password@db.supabase.co:5432/postgres (optional)
```

**Validation** (`src/config/config.py` lines 385-390):
```python
if not cls.SUPABASE_URL:
    missing_vars.append("SUPABASE_URL")
if not cls.SUPABASE_KEY:
    missing_vars.append("SUPABASE_KEY")
```

⚠️ **CRITICAL**: Must be present in both staging and production. App will not start without these.

---

### 2. Primary Provider Configuration (REQUIRED - Usually Same for Both Environments)

#### OpenRouter (Primary Inference Provider)
```env
# Both staging and production typically use the same OpenRouter account
OPENROUTER_API_KEY=sk-or-v1-abc123... (your OpenRouter API key)
OPENROUTER_SITE_URL=https://your-site.com
OPENROUTER_SITE_NAME=Gatewayz AI Gateway
```

**Validation** (`src/config/config.py` lines 389-390):
```python
if not cls.OPENROUTER_API_KEY:
    missing_vars.append("OPENROUTER_API_KEY")
```

⚠️ **CRITICAL**: Must be present in both staging and production. App will not start without this.

---

## Critical Optional Variables (Recommended for Both Environments)

### Payment Processing (If Using Stripe)
```env
# Staging (test mode)
STRIPE_SECRET_KEY=sk_test_... (Stripe test secret key)
STRIPE_WEBHOOK_SECRET=whsec_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...

# Production (live mode)
STRIPE_SECRET_KEY=sk_live_... (Stripe live secret key)
STRIPE_WEBHOOK_SECRET=whsec_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
```

**Purpose**: Credit card processing, subscription management
**If missing**: Payment functionality disabled, users cannot upgrade plans
**Location**: `src/routes/payments.py`

### Email Delivery (If Using Resend)
```env
# Both environments typically use the same Resend account
RESEND_API_KEY=re_abc123...
FROM_EMAIL=noreply@yourdomain.com
APP_NAME=Gatewayz API
APP_URL=https://yourdomain.com (staging) or https://api.yourdomain.com (production)
```

**Purpose**: Send email notifications, password resets, confirmations
**If missing**: Email notifications disabled
**Location**: `src/services/notification.py`

### Environment Identification Variables
```env
# For Sentry error tracking (different projects per environment)
SENTRY_DSN=https://key@sentry.io/staging-project-id (staging)
SENTRY_DSN=https://key@sentry.io/production-project-id (production)
SENTRY_ENVIRONMENT=staging
SENTRY_RELEASE=2.0.3

# For analytics
POSTHOG_API_KEY=phc_abc123...
STATSIG_SERVER_SECRET_KEY=secret_key...
```

---

## Optional Provider API Keys

### Image Generation Providers

#### Google Vertex AI (Image Generation)
```env
GOOGLE_PROJECT_ID=gatewayz-468519 (or your project ID)
GOOGLE_VERTEX_LOCATION=us-central1
GOOGLE_VERTEX_ENDPOINT_ID=6072619212881264640
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
GOOGLE_VERTEX_TRANSPORT=rest
GOOGLE_VERTEX_TIMEOUT=60
```

**Purpose**: Image generation via Stability Diffusion v1.5
**If missing**: Image generation falls back to other providers
**Location**: `src/services/google_vertex_client.py`

#### Fal.ai (Image Generation)
```env
FAL_API_KEY=your-fal-api-key
```

**Purpose**: Alternative image generation provider
**If missing**: Falls back to other image providers

---

### Alternative Inference Providers

These are all **optional** - provide at least one primary inference provider (OpenRouter is included).

#### Featherless
```env
FEATHERLESS_API_KEY=your-featherless-api-key
```
**Models**: Open-source models, good for cost optimization

#### Together AI
```env
TOGETHER_API_KEY=your-together-api-key
```
**Models**: Open-source models, distributed inference

#### Groq
```env
GROQ_API_KEY=your-groq-api-key
```
**Models**: Fast inference, limited model selection

#### DeepInfra
```env
DEEPINFRA_API_KEY=your-deepinfra-api-key
```
**Models**: Open-source models, serverless inference

#### Anthropic (For Direct Claude Access)
```env
ANTHROPIC_API_KEY=your-anthropic-api-key
```
**Models**: Claude models (if not using OpenRouter for Claude routing)

#### XAI (Grok)
```env
XAI_API_KEY=your-xai-api-key
```
**Models**: Grok models

#### Cerebras
```env
CEREBRAS_API_KEY=your-cerebras-api-key
```
**Models**: Fast inference

#### Groq
```env
GROQ_API_KEY=your-groq-api-key
```
**Models**: Fast inference

#### Additional Providers
```env
FIREWORKS_API_KEY=your-fireworks-api-key
CHUTES_API_KEY=your-chutes-api-key
AIMO_API_KEY=your-aimo-api-key
NEAR_API_KEY=your-near-api-key
NEBIUS_API_KEY=your-nebius-api-key
NOVITA_API_KEY=your-novita-api-key
HUG_API_KEY=your-huggingface-token
ANANNAS_API_KEY=your-anannas-api-key
ALPACA_NETWORK_API_KEY=your-alpaca-api-key
CLARIFAI_API_KEY=your-clarifai-api-key
AKASH_API_KEY=your-akash-api-key
MORPHEUS_API_KEY=your-morpheus-api-key
VERCEL_AI_GATEWAY_API_KEY=your-vercel-gateway-api-key
HELICONE_API_KEY=your-helicone-api-key
AI_SDK_API_KEY=your-ai-sdk-api-key
AIHUBMIX_API_KEY=sk-your-aihubmix-api-key
AIHUBMIX_APP_CODE=your-6-digit-code
ALIBABA_CLOUD_API_KEY=your-alibaba-key
ALIBABA_CLOUD_API_KEY_INTERNATIONAL=your-intl-key
ALIBABA_CLOUD_API_KEY_CHINA=your-china-key
CLOUDFLARE_API_TOKEN=your-cloudflare-token
CLOUDFLARE_ACCOUNT_ID=your-account-id
```

**All Optional**: If provider key is missing, that provider simply won't be available
**Purpose**: Provide diverse model selection and failover options
**Location**: `src/services/` (one file per provider: `openrouter_client.py`, `featherless_client.py`, etc.)

---

## Observability & Monitoring Variables

### Prometheus Metrics (For Grafana Integration)
```env
PROMETHEUS_ENABLED=true (enable metrics collection)
PROMETHEUS_SCRAPE_ENABLED=true (enable /metrics endpoint)
PROMETHEUS_REMOTE_WRITE_URL=http://prometheus:9090/api/v1/write (local)
# or for Grafana Cloud:
PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-prod-xx.grafana.net/api/prom/push
GRAFANA_PROMETHEUS_USERNAME=123456 (Grafana instance ID)
GRAFANA_PROMETHEUS_API_KEY=glc_your-api-key
```

**Purpose**: Export metrics for Prometheus/Grafana dashboards
**Recommended**: Enable in both staging and production
**Location**: `src/services/prometheus_metrics.py`, `src/routes/metrics.py`

### Distributed Tracing (OpenTelemetry/Tempo)
```env
TEMPO_ENABLED=false (set to true for tracing)
OTEL_SERVICE_NAME=gatewayz-api
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo:4318 (local)
# or for Grafana Cloud:
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-prod-xx.grafana.net/tempo
```

**Purpose**: Distributed tracing for request debugging
**Optional**: Can be enabled in staging/production
**Location**: `src/config/opentelemetry_config.py`

### Structured Logging (Loki)
```env
LOKI_ENABLED=false (set to true for log aggregation)
LOKI_PUSH_URL=http://loki:3100/loki/api/v1/push (local)
# or for Grafana Cloud:
LOKI_PUSH_URL=https://logs-prod-xx.grafana.net/loki/api/v1/push
```

**Purpose**: Aggregate logs to Loki for searching in Grafana
**Optional**: Can be enabled in staging/production
**Location**: `src/config/logging_config.py`

### Redis Cache (Required for Rate Limiting)
```env
# Both environments should have Redis
REDIS_ENABLED=true
REDIS_URL=redis://localhost:6379 (local/staging)
# or for production:
REDIS_URL=redis://redis-prod-xxxx.railway.internal:6379 (Railway)
REDIS_MAX_CONNECTIONS=50
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
```

**Purpose**: Rate limiting, response caching, metrics aggregation
**Required**: For production (rate limiting essential)
**Recommended**: For staging (test rate limiting behavior)
**Location**: `src/config/redis_config.py`, `src/services/rate_limiting.py`

---

## API Key Security Configuration

### Key Encryption (REQUIRED for Production)
```env
# For production, you MUST set these for API key encryption
KEY_HASH_SALT=your-32-character-hex-string (generate with: python -c "import secrets; print(secrets.token_hex(32))")

# Optional but recommended for extra security
KEY_VERSION=1
KEYRING_1=your-base64-fernet-key (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

**Purpose**: Encrypt user API keys stored in database
**Critical for Production**: Without this, API keys stored in plaintext
**Location**: `src/security/security.py`, `src/db/api_keys.py`

---

## Staging vs Production Comparison Table

| Variable | Staging | Production | Purpose |
|----------|---------|------------|---------|
| `APP_ENV` | `staging` | `production` | Environment identifier |
| `SUPABASE_URL` | `staging-project.supabase.co` | `production-project.supabase.co` | Database URL (separate projects) |
| `SUPABASE_KEY` | staging key | production key | Database auth (different keys) |
| `OPENROUTER_API_KEY` | Same account | Same account | Primary inference provider |
| `STRIPE_SECRET_KEY` | `sk_test_...` | `sk_live_...` | Payment processing (TEST vs LIVE) |
| `SENTRY_DSN` | staging project ID | production project ID | Error tracking (separate projects) |
| `REDIS_URL` | local or staging instance | production instance | Rate limiting cache |
| `KEY_HASH_SALT` | optional | REQUIRED | API key encryption salt |
| `KEYRING_1` | optional | REQUIRED | API key encryption key |
| Provider Keys | same or test values | production values | All other provider APIs (optional) |

---

## Complete Configuration Example

### Staging Environment (.env.staging)
```env
# Environment
APP_ENV=staging

# Database (Staging Supabase)
SUPABASE_URL=https://staging-proj.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Primary Provider
OPENROUTER_API_KEY=sk-or-v1-abc123...
OPENROUTER_SITE_URL=https://staging.yourdomain.com
OPENROUTER_SITE_NAME=Gatewayz Staging API

# Payment (Stripe Test Mode)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...

# Email
RESEND_API_KEY=re_...
FROM_EMAIL=noreply@staging.yourdomain.com
APP_NAME=Gatewayz Staging
APP_URL=https://staging.yourdomain.com

# Cache & Rate Limiting
REDIS_ENABLED=true
REDIS_URL=redis://localhost:6379
REDIS_MAX_CONNECTIONS=50

# Monitoring
PROMETHEUS_ENABLED=true
PROMETHEUS_SCRAPE_ENABLED=true
SENTRY_DSN=https://key@sentry.io/staging-project-id
SENTRY_ENVIRONMENT=staging
SENTRY_RELEASE=2.0.3

# Error Tracking
SENTRY_ENABLED=true
SENTRY_TRACES_SAMPLE_RATE=1.0

# Optional: Observability
TEMPO_ENABLED=false
LOKI_ENABLED=false

# API Key Security
KEY_HASH_SALT=your-32-char-hex-string
# KEYRING_1=optional-in-staging

# Optional Providers (for testing)
FEATHERLESS_API_KEY=test-key
TOGETHER_API_KEY=test-key
GROQ_API_KEY=test-key
```

### Production Environment (.env.production)
```env
# Environment
APP_ENV=production

# Database (Production Supabase)
SUPABASE_URL=https://production-proj.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Primary Provider
OPENROUTER_API_KEY=sk-or-v1-abc123...
OPENROUTER_SITE_URL=https://api.yourdomain.com
OPENROUTER_SITE_NAME=Gatewayz API Gateway

# Payment (Stripe Live Mode)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...

# Email
RESEND_API_KEY=re_...
FROM_EMAIL=noreply@yourdomain.com
APP_NAME=Gatewayz
APP_URL=https://api.yourdomain.com

# Cache & Rate Limiting (CRITICAL)
REDIS_ENABLED=true
REDIS_URL=redis://redis-prod-xxx.railway.internal:6379
REDIS_MAX_CONNECTIONS=100

# Monitoring
PROMETHEUS_ENABLED=true
PROMETHEUS_SCRAPE_ENABLED=true
PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-prod-xx.grafana.net/api/prom/push
GRAFANA_PROMETHEUS_USERNAME=123456
GRAFANA_PROMETHEUS_API_KEY=glc_...
SENTRY_DSN=https://key@sentry.io/production-project-id
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=2.0.3

# Error Tracking
SENTRY_ENABLED=true
SENTRY_TRACES_SAMPLE_RATE=0.1  # Reduce in production

# Optional: Observability (Grafana Cloud)
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=https://tempo-prod-xx.grafana.net/tempo
LOKI_ENABLED=true
LOKI_PUSH_URL=https://logs-prod-xx.grafana.net/loki/api/v1/push

# API Key Security (REQUIRED for Production)
KEY_HASH_SALT=your-32-char-hex-string-production
KEYRING_1=your-base64-fernet-key-production

# All Provider Keys (for failover & diversity)
FEATHERLESS_API_KEY=production-key
TOGETHER_API_KEY=production-key
GROQ_API_KEY=production-key
ANTHROPIC_API_KEY=production-key
CEREBRAS_API_KEY=production-key
```

---

## Deployment Platform Instructions

### Railway
```bash
# Set environment variables in Railway dashboard:
# Settings → Variables → Add Variable

# Or via CLI:
railway variables set APP_ENV=staging
railway variables set SUPABASE_URL=https://staging.supabase.co
railway variables set SUPABASE_KEY=your-key
# ... etc
```

### Vercel
```bash
# Set environment variables:
vercel env add APP_ENV
vercel env add SUPABASE_URL
vercel env add SUPABASE_KEY
# ... etc

# Set environment-specific variables:
vercel env add SUPABASE_URL --environments production
vercel env add SUPABASE_URL --environments staging
```

### AWS Lambda / Environment File
```bash
# Create .env files in deployment:
# .env.staging
# .env.production
```

---

## Pre-Deployment Validation Checklist

### For Staging
- ✅ `APP_ENV=staging` is set
- ✅ `SUPABASE_URL` and `SUPABASE_KEY` pointing to staging database
- ✅ `OPENROUTER_API_KEY` is valid
- ✅ `REDIS_URL` is accessible
- ✅ `STRIPE_SECRET_KEY` in test mode (`sk_test_`)
- ✅ `KEY_HASH_SALT` is set (optional but recommended)
- ✅ `SENTRY_DSN` points to staging project

### For Production
- ✅ `APP_ENV=production` is set
- ✅ `SUPABASE_URL` and `SUPABASE_KEY` pointing to production database
- ✅ `OPENROUTER_API_KEY` is valid and has sufficient credits
- ✅ `REDIS_URL` is accessible and healthy
- ✅ `STRIPE_SECRET_KEY` in live mode (`sk_live_`)
- ✅ `KEY_HASH_SALT` is set (REQUIRED)
- ✅ `KEYRING_1` is set (REQUIRED)
- ✅ `TESTING` environment variable is NOT set
- ✅ `SENTRY_DSN` points to production project
- ✅ All critical API keys are valid and active
- ✅ HTTPS is enforced
- ✅ Rate limiting is enabled
- ✅ Error tracking is enabled

### Anti-Pattern Validation
```bash
# Make sure these are NEVER in production:
grep -r "TESTING=true" .env.production  # Should return nothing
grep -r "sk_test_" .env.production      # Should return nothing (use sk_live_)
grep -r "localhost" .env.production     # Should return nothing (use full URLs)
grep -r "development" .env.production   # Should only be in APP_ENV=production context
```

---

## Security Best Practices

### 1. Never Commit Secrets
```bash
# Add to .gitignore
echo ".env" >> .gitignore
echo ".env.staging" >> .gitignore
echo ".env.production" >> .gitignore
echo ".env.local" >> .gitignore
```

### 2. Use Separate Database Projects
- **Staging**: Separate Supabase project (safe for destructive testing)
- **Production**: Separate Supabase project (critical data)

### 3. Use Separate Stripe Accounts
- **Staging**: Stripe test mode (`sk_test_`, `pk_test_`)
- **Production**: Stripe live mode (`sk_live_`, `pk_live_`)

### 4. Rotate Keys Regularly
```bash
# Every 30-90 days, rotate:
- OPENROUTER_API_KEY
- STRIPE_SECRET_KEY
- SUPABASE_KEY
- All provider API keys
```

### 5. Enable API Key Encryption
- Always set `KEY_HASH_SALT` in production
- Always set `KEYRING_1` for Fernet encryption

### 6. Use Environment-Specific Monitoring
- Different Sentry projects for staging vs production
- Different Grafana dashboards per environment

---

## Summary

### Required for Both Environments (Same Variable Names, Different Values)
1. **SUPABASE_URL** - Database URL
2. **SUPABASE_KEY** - Database authentication
3. **OPENROUTER_API_KEY** - Primary inference provider
4. **APP_ENV** - Environment identifier

### Highly Recommended
5. **REDIS_URL** - Rate limiting & caching
6. **STRIPE_*** keys (if using payments)
7. **SENTRY_DSN** (if using error tracking)
8. **KEY_HASH_SALT** - API key encryption

### Optional
- Alternative provider API keys (Featherless, Together, Groq, etc.)
- Observability (Tempo, Loki, Prometheus remote write)
- Email configuration (Resend)
- Analytics (PostHog, Statsig)

### The Short Answer
**There is no single `staging_api_key` or `production_api_key` variable.** Instead:
- Use different Supabase database instances
- Use different provider API credentials
- Use `APP_ENV=staging` or `APP_ENV=production` to control behavior
- All other variables remain the same names but with environment-specific values

---

**Questions?** Refer to:
- `docs/environment.md` - Environment variable overview
- `.env.example` - Example configuration template
- `src/config/config.py` - Configuration validation code

