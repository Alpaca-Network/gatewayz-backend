# How Staging vs Production Differentiation Works

Complete explanation of how your app knows whether it's running in staging or production, and how the security middleware only activates in staging.

---

## 🔑 The Key: `APP_ENV` Environment Variable

Everything is controlled by a **single environment variable**: `APP_ENV`

```
Railway Production Environment:
APP_ENV=production

Railway Staging Environment:
APP_ENV=staging

Local Development:
APP_ENV=development
```

---

## 📊 How It Works: Step by Step

### Step 1: Environment Variable is Set in Railway

**Production Environment:**
```bash
railway environment production
railway variables set APP_ENV=production
```

**Staging Environment:**
```bash
railway environment staging
railway variables set APP_ENV=staging
```

Each Railway environment has its own separate set of environment variables!

---

### Step 2: Config Reads Environment Variable

When your app starts, `src/config/config.py` reads the environment:

```python
# src/config/config.py (Line 97-100)

class Config:
    # Read APP_ENV from environment variable
    APP_ENV = os.environ.get("APP_ENV", "development")

    # Set boolean flags based on APP_ENV
    IS_PRODUCTION = APP_ENV == "production"   # True only if APP_ENV=production
    IS_STAGING = APP_ENV == "staging"         # True only if APP_ENV=staging
    IS_DEVELOPMENT = APP_ENV == "development" # True only if APP_ENV=development
```

**In Production:**
```python
Config.APP_ENV = "production"
Config.IS_PRODUCTION = True
Config.IS_STAGING = False
Config.IS_DEVELOPMENT = False
```

**In Staging:**
```python
Config.APP_ENV = "staging"
Config.IS_PRODUCTION = False
Config.IS_STAGING = True
Config.IS_DEVELOPMENT = False
```

---

### Step 3: Middleware Checks Environment

The security middleware checks `APP_ENV` **before** doing anything:

```python
# src/middleware/staging_security.py (Line 86-88)

async def dispatch(self, request: Request, call_next):
    """Process request and enforce staging security."""

    # THE KEY CHECK: Only apply in staging environment
    if Config.APP_ENV != "staging":
        # If NOT staging, skip ALL security checks
        return await call_next(request)

    # If we get here, APP_ENV == "staging"
    # Now check for token...
```

**What this means:**

```python
# In Production (APP_ENV=production)
if "production" != "staging":  # True!
    return await call_next(request)  # Skip security entirely
    # Request goes straight to your API endpoint
    # NO token check happens!

# In Staging (APP_ENV=staging)
if "staging" != "staging":  # False!
    # Skip this line
# Continue to token verification below
```

---

## 🎯 Complete Flow Comparison

### Production Request Flow

```
┌─────────────────────────────────────────────────────────┐
│ Railway Production Environment                          │
│ APP_ENV=production                                      │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
                  User Request
                         ↓
┌─────────────────────────────────────────────────────────┐
│ StagingSecurityMiddleware.dispatch()                   │
│                                                         │
│ if Config.APP_ENV != "staging":                        │
│    if "production" != "staging": ✅ TRUE               │
│       return await call_next(request)                  │
│       → SKIP ALL SECURITY CHECKS                       │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
                  Your API Endpoint
                         │
                         ↓
                   Response (200 OK)

NO TOKEN REQUIRED IN PRODUCTION!
```

### Staging Request Flow

```
┌─────────────────────────────────────────────────────────┐
│ Railway Staging Environment                             │
│ APP_ENV=staging                                         │
│ STAGING_ACCESS_TOKEN=staging_DrN4Pw87...               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
                  User Request
                  (with X-Staging-Access-Token header)
                         │
                         ↓
┌─────────────────────────────────────────────────────────┐
│ StagingSecurityMiddleware.dispatch()                   │
│                                                         │
│ if Config.APP_ENV != "staging":                        │
│    if "staging" != "staging": ❌ FALSE                 │
│       (skip this block)                                │
│                                                         │
│ Continue to token verification:                        │
│   auth_header = request.headers.get("X-Staging...")   │
│   if auth_header != self.staging_token:               │
│      ❌ Deny if wrong                                  │
│   ✅ Allow if correct                                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ↓
                  Your API Endpoint
                         │
                         ↓
                   Response (200 OK)

TOKEN REQUIRED IN STAGING!
```

---

## 🔧 Railway Configuration

### Production Environment Variables

```bash
# Railway Production Environment
APP_ENV=production                           # ← KEY: Makes it production
SUPABASE_URL=https://prod.supabase.co       # Production database
STRIPE_SECRET_KEY=sk_live_...               # Live Stripe
SENTRY_ENVIRONMENT=production
# NO STAGING_ACCESS_TOKEN (not needed)
```

**Result:** Security middleware is DISABLED

### Staging Environment Variables

```bash
# Railway Staging Environment
APP_ENV=staging                                              # ← KEY: Makes it staging
STAGING_ACCESS_TOKEN=staging_DrN4Pw87LpFTTCyAPGQ5aERDJ...   # ← Security token
SUPABASE_URL=https://staging.supabase.co                    # Staging database
STRIPE_SECRET_KEY=sk_test_...                               # Test Stripe
SENTRY_ENVIRONMENT=staging
```

**Result:** Security middleware is ENABLED

---

## 📋 Side-by-Side Comparison

| Setting | Production | Staging |
|---------|-----------|---------|
| **APP_ENV** | `production` | `staging` |
| **Security Middleware** | ❌ Disabled | ✅ Enabled |
| **Token Required** | ❌ No | ✅ Yes |
| **STAGING_ACCESS_TOKEN** | Not set | `staging_DrN4Pw87...` |
| **Database** | Production Supabase | Staging Supabase |
| **Stripe** | Live keys | Test keys |
| **Public Access** | ✅ Yes (normal API) | ❌ No (token required) |

---

## 🧪 Testing the Differentiation

### Test Production (No Token Required)

```bash
# Production - works without token
curl https://api.gatewayz.ai/v1/models

# Response: 200 OK
# [{"id": "gpt-4", ...}, ...]
```

### Test Staging (Token Required)

```bash
# Staging - without token (fails)
curl https://staging.gatewayz.ai/v1/models

# Response: 403 Forbidden
# {
#   "error": "Staging Access Denied",
#   "message": "Missing X-Staging-Access-Token header"
# }

# Staging - with token (works)
curl -H "X-Staging-Access-Token: staging_DrN4Pw87..." \
     https://staging.gatewayz.ai/v1/models

# Response: 200 OK
# [{"id": "gpt-4", ...}, ...]
```

---

## 🔍 How to Verify Which Environment You're In

### Method 1: Check Environment Variable

```bash
# In Railway
railway environment production
railway variables | grep APP_ENV
# Output: APP_ENV=production

railway environment staging
railway variables | grep APP_ENV
# Output: APP_ENV=staging
```

### Method 2: Check Logs on Startup

When your app starts, look for this in the logs:

```bash
# Production logs
railway logs --environment production | grep -i environment

# You'll see:
# "Environment: production"
# "SENTRY_ENVIRONMENT=production"

# Staging logs
railway logs --environment staging | grep -i environment

# You'll see:
# "Environment: staging"
# "🔒 Staging security middleware enabled"
# "Staging security enabled: Access Token"
```

### Method 3: Call the Health Endpoint

```bash
# Production
curl https://api.gatewayz.ai/health

# Response includes:
# {
#   "status": "healthy",
#   "environment": "production"  ← Here!
# }

# Staging
curl https://staging.gatewayz.ai/health

# Response includes:
# {
#   "status": "healthy",
#   "environment": "staging"  ← Here!
# }
```

---

## 💡 Code at Startup

Here's what happens when your app starts in each environment:

### Production Startup

```python
# 1. Railway sets environment variable
os.environ["APP_ENV"] = "production"

# 2. Config reads it
Config.APP_ENV = "production"
Config.IS_PRODUCTION = True
Config.IS_STAGING = False

# 3. Middleware initializes
class StagingSecurityMiddleware:
    def __init__(self, app):
        if Config.APP_ENV == "staging":  # False!
            # This logging block doesn't run
            pass

# 4. On every request
async def dispatch(self, request, call_next):
    if Config.APP_ENV != "staging":  # True!
        return await call_next(request)  # Skip security
    # Never reaches token check in production
```

**Logs show:**
```
✅ Application startup complete!
Environment: production
(No staging security messages)
```

### Staging Startup

```python
# 1. Railway sets environment variable
os.environ["APP_ENV"] = "staging"
os.environ["STAGING_ACCESS_TOKEN"] = "staging_DrN4Pw87..."

# 2. Config reads it
Config.APP_ENV = "staging"
Config.IS_PRODUCTION = False
Config.IS_STAGING = True

# 3. Middleware initializes
class StagingSecurityMiddleware:
    def __init__(self, app):
        self.staging_token = os.getenv("STAGING_ACCESS_TOKEN")
        if Config.APP_ENV == "staging":  # True!
            logger.info("Staging security enabled: Access Token")

# 4. On every request
async def dispatch(self, request, call_next):
    if Config.APP_ENV != "staging":  # False!
        # Skip this line
    # Continues to token verification below
    if self.staging_token:
        auth_header = request.headers.get("X-Staging-Access-Token")
        # ... token check happens
```

**Logs show:**
```
🔒 Staging security middleware enabled
Staging security enabled: Access Token
✅ Application startup complete!
Environment: staging
```

---

## 🎯 The Critical Lines of Code

**The single line that makes the difference:**

```python
# src/middleware/staging_security.py - Line 87

if Config.APP_ENV != "staging":
    return await call_next(request)
```

**This line means:**
- If `APP_ENV` is **anything other than "staging"** → Skip security
- If `APP_ENV` is **"staging"** → Apply security

**So:**
- Production (`APP_ENV=production`) → Security skipped ✅
- Staging (`APP_ENV=staging`) → Security enforced 🔒
- Development (`APP_ENV=development`) → Security skipped ✅

---

## 🔐 Security Benefits of This Approach

1. **No Risk to Production**
   - Production code is identical to staging
   - But production security is never active
   - No chance of accidentally blocking production users

2. **Environment-Specific**
   - Each environment is configured independently
   - Production can't accidentally use staging token
   - Staging can't accidentally expose production data

3. **Easy to Debug**
   - Clear logs showing which environment
   - Easy to verify in Railway dashboard
   - Simple boolean check

4. **Flexible**
   - Easy to add more environments (dev, qa, etc.)
   - Easy to enable/disable security
   - Easy to test both modes

---

## 📊 Summary Table

| Environment | APP_ENV | Security Active? | Token Required? | Access |
|-------------|---------|------------------|-----------------|--------|
| **Production** | `production` | ❌ No | ❌ No | 🌐 Public |
| **Staging** | `staging` | ✅ Yes | ✅ Yes | 🔒 Private |
| **Local Dev** | `development` | ❌ No | ❌ No | 💻 Local |

---

## 🎯 Key Takeaway

**The middleware is in your code for ALL environments (production, staging, development), but it ONLY activates when `APP_ENV=staging`.**

Think of it like a light switch:
- Production: Switch is OFF (middleware inactive)
- Staging: Switch is ON (middleware active, checking tokens)

The same code is deployed everywhere, but it behaves differently based on the `APP_ENV` environment variable!

---

## ✅ Verification Checklist

To confirm your environments are configured correctly:

**Production:**
- [ ] `APP_ENV=production` set in Railway
- [ ] `STAGING_ACCESS_TOKEN` NOT set (or empty)
- [ ] API accessible without token: `curl https://api.gatewayz.ai/v1/models` → 200 OK
- [ ] Logs don't show "Staging security enabled"

**Staging:**
- [ ] `APP_ENV=staging` set in Railway
- [ ] `STAGING_ACCESS_TOKEN=staging_...` set
- [ ] API NOT accessible without token: `curl https://staging.gatewayz.ai/v1/models` → 403
- [ ] API accessible WITH token: `curl -H "X-Staging-Access-Token: ..." ...` → 200 OK
- [ ] Logs show "🔒 Staging security middleware enabled"

---

**Questions?** The key is just one environment variable: `APP_ENV`. That's how your app knows which environment it's in and whether to enforce security!
