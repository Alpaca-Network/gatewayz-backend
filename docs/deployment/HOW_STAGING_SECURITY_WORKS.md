# How Staging Security Verification Works

Detailed explanation of how the token verification process works in your staging environment.

---

## 📍 Where the Verification Happens

The verification happens in the **middleware layer** before your request reaches any endpoint.

```
User Request
    ↓
FastAPI receives request
    ↓
StagingSecurityMiddleware (CHECKS TOKEN HERE)
    ↓ (if valid)
Your API Endpoint (/v1/chat/completions, etc.)
    ↓
Response back to user
```

---

## 🔍 Step-by-Step Token Verification

### Step 1: Middleware Initialization (On Startup)

When your FastAPI app starts, the middleware reads the token from environment variables:

```python
# src/middleware/staging_security.py (Line 52-55)

def __init__(self, app):
    super().__init__(app)
    # Read the token from environment variable
    self.staging_token = os.getenv("STAGING_ACCESS_TOKEN")
    # Example: self.staging_token = "staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw"
```

**What happens:**
1. Middleware loads when app starts
2. Reads `STAGING_ACCESS_TOKEN` from Railway environment variables
3. Stores it in `self.staging_token` for later comparison
4. Logs: "Staging security enabled: Access Token"

---

### Step 2: Request Arrives

When a user makes a request to your API:

```bash
curl -X POST https://staging.gatewayz.ai/v1/chat/completions \
  -H "X-Staging-Access-Token: staging_DrN4Pw87..." \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -d '{"model":"gpt-4","messages":[...]}'
```

**Request headers:**
```
X-Staging-Access-Token: staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw
Authorization: Bearer gw_test_pro_key_12345
Content-Type: application/json
```

---

### Step 3: Middleware Intercepts Request

**Before** your endpoint handler runs, the middleware's `dispatch()` method is called:

```python
# src/middleware/staging_security.py (Line 83-139)

async def dispatch(self, request: Request, call_next):
    """Process request and enforce staging security."""

    # Check 1: Only run in staging environment
    if Config.APP_ENV != "staging":
        return await call_next(request)  # Skip if not staging

    # Check 2: Skip security for health checks
    if request.url.path in self.ALLOWED_PATHS:
        return await call_next(request)  # Allow /health, /ping, etc.

    # Check 3: Verify token (THIS IS WHERE VERIFICATION HAPPENS)
    if self.staging_token:
        # Get the token from request headers
        auth_header = request.headers.get("X-Staging-Access-Token")

        # Check if token is missing
        if not auth_header:
            # DENY: Token not provided
            return self._access_denied_response(
                reason="Missing X-Staging-Access-Token header"
            )

        # Check if token matches
        if auth_header != self.staging_token:
            # DENY: Token is wrong
            return self._access_denied_response(
                reason="Invalid access token"
            )

    # All checks passed - allow request to continue
    return await call_next(request)
```

---

## 🔐 The Actual Verification (Line 127)

This is the critical line where verification happens:

```python
# Line 127
if auth_header != self.staging_token:
```

**What this does:**
1. `auth_header` = Token from user's request header
2. `self.staging_token` = Token stored from Railway environment variable
3. Compares them using Python's `!=` (not equal) operator
4. If they **don't match** → Deny access
5. If they **match** → Continue to next line (allow access)

**Example:**

```python
# What's stored in Railway
self.staging_token = "staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw"

# What user sent
auth_header = "staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw"

# Comparison
if "staging_DrN4Pw87..." != "staging_DrN4Pw87...":
    # False! They match, so this block is NOT executed

# Continues to line 139: return await call_next(request)
# ✅ Request is allowed!
```

---

## 📊 Complete Request Flow Diagram

### Scenario 1: Valid Token ✅

```
1. Request arrives with header:
   X-Staging-Access-Token: staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw

2. Middleware checks:
   ┌─────────────────────────────────────┐
   │ Is APP_ENV == "staging"?            │
   │ ✅ Yes (APP_ENV=staging in Railway) │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Is path in ALLOWED_PATHS?           │
   │ ❌ No (/v1/chat/completions)        │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Is STAGING_ACCESS_TOKEN set?        │
   │ ✅ Yes                              │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Get X-Staging-Access-Token header   │
   │ ✅ Found: "staging_DrN4Pw87..."    │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Does header == stored token?        │
   │ ✅ Yes! They match!                 │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ return await call_next(request)     │
   │ → Continue to API endpoint          │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ /v1/chat/completions handler runs   │
   │ → Returns chat response             │
   └─────────────────────────────────────┘
                 ↓
   User receives: 200 OK with chat completion
```

### Scenario 2: Missing Token ❌

```
1. Request arrives WITHOUT header:
   (No X-Staging-Access-Token header)

2. Middleware checks:
   ┌─────────────────────────────────────┐
   │ Is APP_ENV == "staging"?            │
   │ ✅ Yes                              │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Is path in ALLOWED_PATHS?           │
   │ ❌ No                               │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Is STAGING_ACCESS_TOKEN set?        │
   │ ✅ Yes                              │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Get X-Staging-Access-Token header   │
   │ ❌ None! Header not found           │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ if not auth_header:                 │
   │ ✅ True! Header is missing          │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ return _access_denied_response()    │
   │ → Return 403 Forbidden              │
   └─────────────────────────────────────┘
                 ↓
   User receives: 403 Forbidden
   {
     "error": "Staging Access Denied",
     "message": "Missing X-Staging-Access-Token header"
   }

   API endpoint is NEVER called!
```

### Scenario 3: Wrong Token ❌

```
1. Request arrives with WRONG token:
   X-Staging-Access-Token: wrong_token_12345

2. Middleware checks:
   ┌─────────────────────────────────────┐
   │ Get X-Staging-Access-Token header   │
   │ ✅ Found: "wrong_token_12345"      │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Does header == stored token?        │
   │ "wrong_token_12345" !=             │
   │ "staging_DrN4Pw87..."              │
   │ ❌ No! They don't match            │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ return _access_denied_response()    │
   │ → Return 403 Forbidden              │
   └─────────────────────────────────────┘
                 ↓
   User receives: 403 Forbidden
   {
     "error": "Staging Access Denied",
     "message": "Invalid access token"
   }

   API endpoint is NEVER called!
```

### Scenario 4: Health Check (Bypass) ✅

```
1. Request to health endpoint:
   GET /health

2. Middleware checks:
   ┌─────────────────────────────────────┐
   │ Is APP_ENV == "staging"?            │
   │ ✅ Yes                              │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ Is path in ALLOWED_PATHS?           │
   │ ✅ Yes! "/health" is allowed        │
   └─────────────────────────────────────┘
                 ↓
   ┌─────────────────────────────────────┐
   │ return await call_next(request)     │
   │ → Skip all security checks          │
   └─────────────────────────────────────┘
                 ↓
   /health endpoint runs
                 ↓
   User receives: 200 OK
   {"status": "healthy", ...}

   No token required!
```

---

## 🧪 Real Examples

### Example 1: Successful Request

```bash
curl -X POST https://staging.gatewayz.ai/v1/models \
  -H "X-Staging-Access-Token: staging_DrN4Pw87LpFTTCyAPGQ5aERDJ84sVWCebPgB4Y7ClKw"

# What happens:
# 1. Request arrives with header
# 2. Middleware extracts: auth_header = "staging_DrN4Pw87..."
# 3. Compares: "staging_DrN4Pw87..." == "staging_DrN4Pw87..." ✅
# 4. Allows request to continue
# 5. Returns: 200 OK with list of models
```

### Example 2: Failed Request (No Token)

```bash
curl -X POST https://staging.gatewayz.ai/v1/models

# What happens:
# 1. Request arrives WITHOUT header
# 2. Middleware checks: auth_header = None
# 3. if not None: → True (header is missing)
# 4. Returns 403 immediately
# 5. Response:
# {
#   "error": "Staging Access Denied",
#   "message": "Missing X-Staging-Access-Token header",
#   "hint": "Contact your team administrator for access credentials"
# }
```

### Example 3: Failed Request (Wrong Token)

```bash
curl -X POST https://staging.gatewayz.ai/v1/models \
  -H "X-Staging-Access-Token: wrong_token"

# What happens:
# 1. Request arrives with header
# 2. Middleware extracts: auth_header = "wrong_token"
# 3. Compares: "wrong_token" == "staging_DrN4Pw87..." ❌
# 4. Returns 403 immediately
# 5. Response:
# {
#   "error": "Staging Access Denied",
#   "message": "Invalid access token"
# }
```

---

## 🔒 Security Features

### 1. Token Stored Securely

```python
# Token is NEVER in your code
# It's stored as an environment variable in Railway
self.staging_token = os.getenv("STAGING_ACCESS_TOKEN")

# Railway → Environment Variables → STAGING_ACCESS_TOKEN
# Only accessible to Railway and your deployed app
```

### 2. Constant-Time Comparison (Could Be Improved)

**Current implementation:**
```python
if auth_header != self.staging_token:  # Simple comparison
```

**More secure version (prevents timing attacks):**
```python
import secrets

if not secrets.compare_digest(auth_header, self.staging_token):
    # Constant-time comparison
    # Takes same time whether tokens match or not
```

### 3. Logging

Every denied access attempt is logged:

```python
logger.warning(
    f"Staging access denied: Invalid access token",
    extra={
        "client_ip": self._get_client_ip(request),
        "path": request.url.path,
        "token_prefix": auth_header[:10] + "..."  # Only first 10 chars
    }
)
```

**In Railway logs:**
```
⚠️  WARNING: Staging access denied: Invalid access token
    client_ip: 203.0.113.45
    path: /v1/chat/completions
    token_prefix: wrong_toke...
```

---

## 📍 Paths That Bypass Security

These paths are **always accessible** without a token:

```python
# Line 50
ALLOWED_PATHS = {"/health", "/", "/ping", "/docs", "/redoc", "/openapi.json"}
```

**Why?**
- `/health` - Monitoring systems need to check if app is alive
- `/ping` - Health checks
- `/` - Root endpoint (just info)
- `/docs` - Swagger API documentation
- `/redoc` - Alternative API docs
- `/openapi.json` - OpenAPI schema

**Example:**
```bash
# These work WITHOUT token:
curl https://staging.gatewayz.ai/health        # ✅ Works
curl https://staging.gatewayz.ai/ping          # ✅ Works
curl https://staging.gatewayz.ai/docs          # ✅ Works

# These require token:
curl https://staging.gatewayz.ai/v1/models     # ❌ 403 Forbidden
curl https://staging.gatewayz.ai/v1/chat/...   # ❌ 403 Forbidden
```

---

## 🎯 How to Verify It's Working

### Test 1: Check Middleware is Loaded

```bash
# Check Railway logs after deployment
railway logs --environment staging | grep "Staging security"

# Should see:
# "🔒 Staging security middleware enabled"
# "Staging security enabled: Access Token"
```

### Test 2: Test Without Token

```bash
curl -i https://staging.gatewayz.ai/v1/models

# Should return:
# HTTP/1.1 403 Forbidden
# X-Environment: staging
# X-Access-Denied-Reason: Missing X-Staging-Access-Token header
#
# {
#   "error": "Staging Access Denied",
#   "message": "Access to this staging/test environment is restricted: Missing X-Staging-Access-Token header"
# }
```

### Test 3: Test With Valid Token

```bash
curl -i -H "X-Staging-Access-Token: staging_DrN4Pw87..." \
     https://staging.gatewayz.ai/v1/models

# Should return:
# HTTP/1.1 200 OK
# [list of models...]
```

### Test 4: Test Health Check (No Token)

```bash
curl -i https://staging.gatewayz.ai/health

# Should return:
# HTTP/1.1 200 OK
# {"status": "healthy", ...}
```

---

## 🔍 Debugging: How to See What's Happening

### Enable Debug Logging

The middleware already logs warnings when access is denied. View them:

```bash
# View Railway logs
railway logs --environment staging --follow

# Watch for:
# "Staging access denied: Missing access token"
# "Staging access denied: Invalid access token"
```

### Test with Verbose Curl

```bash
# See all headers sent and received
curl -v -H "X-Staging-Access-Token: your-token" \
     https://staging.gatewayz.ai/v1/models

# Output shows:
# > X-Staging-Access-Token: your-token  (what you sent)
# < HTTP/1.1 200 OK                     (response code)
# < X-Environment: staging              (custom header)
```

---

## 💡 Summary

**How token verification works:**

1. **Startup:** Middleware reads `STAGING_ACCESS_TOKEN` from Railway env vars
2. **Request:** User sends request with `X-Staging-Access-Token` header
3. **Intercept:** Middleware intercepts request before endpoint
4. **Extract:** Gets token from `X-Staging-Access-Token` header
5. **Compare:** Compares header value with stored token (Line 127)
6. **Decision:**
   - ✅ Match → Allow request to continue to endpoint
   - ❌ No match → Return 403 Forbidden immediately
   - ⚪ Health check path → Skip all checks

**Key takeaway:** The verification happens **before** your API endpoint code runs. If the token doesn't match, your endpoint code is never executed.

---

## 📖 Related Files

- **Middleware code:** `src/middleware/staging_security.py`
- **Main app:** `src/main.py` (line 229 - where middleware is added)
- **Your token:** `STAGING_SECURITY_SETUP.md`
- **Test script:** `scripts/test-staging-security.sh`

---

**Questions?** This is the exact code flow. The verification is simple but effective: compare the header value with the environment variable value. If they don't match, deny access.
