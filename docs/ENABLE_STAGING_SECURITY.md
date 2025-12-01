ot# How to Enable Staging Security

Quick guide to enable staging API security in your application.

## Option 1: Enable Custom Authentication Token (Recommended)

### Step 1: Generate Token

```bash
python3 -c "import secrets; print('staging_' + secrets.token_urlsafe(32))"
```

Example output: `staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1`

### Step 2: Set in Railway

```bash
# Switch to staging environment
railway environment switch staging

# Set the token
railway variables set STAGING_ACCESS_TOKEN="staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1"
```

### Step 3: Add Middleware to App

Edit `src/main.py` and add the middleware:

```python
from src.middleware.staging_security import StagingSecurityMiddleware

def create_app() -> FastAPI:
    app = FastAPI(
        title="Gatewayz API",
        version="2.0.3",
        # ... your existing config ...
    )

    # Add staging security middleware
    # (Add this early in the middleware stack, after CORS)
    app.add_middleware(StagingSecurityMiddleware)

    # ... rest of your middleware and routes ...

    return app
```

### Step 4: Commit and Deploy

```bash
git add src/main.py src/middleware/staging_security.py
git commit -m "Add staging security middleware"
git push origin main

# This will auto-deploy to staging
# Wait for deployment to complete (~3 minutes)
```

### Step 5: Test It

```bash
# Without token - should fail with 403
curl https://your-staging-domain/v1/models

# Expected response:
# {
#   "error": "Staging Access Denied",
#   "message": "Access to this staging/test environment is restricted: Missing X-Staging-Access-Token header"
# }

# With token - should work
curl https://your-staging-domain/v1/models \
  -H "X-Staging-Access-Token: staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1"

# Expected: Returns list of models

# Health check - should always work (bypasses security)
curl https://your-staging-domain/health

# Expected: {"status": "healthy", ...}
```

### Step 6: Share Token with Team

Share the token securely with your team:

**For Testing:**
```bash
# All API requests must include the token
curl https://staging.gatewayz.ai/v1/chat/completions \
  -H "X-Staging-Access-Token: staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1" \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-3.5-turbo","messages":[{"role":"user","content":"test"}]}'
```

**For Postman/API Clients:**
Add a header:
- Key: `X-Staging-Access-Token`
- Value: `staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1`

✅ **Done!** Staging API is now protected.

---

## Option 2: Enable IP Whitelisting

### Step 1: Get IP Addresses

```bash
# Get your IP
curl https://ifconfig.me

# Or
curl https://api.ipify.org
```

Ask team members to do the same.

### Step 2: Set in Railway

```bash
railway environment switch staging
railway variables set STAGING_ALLOWED_IPS="203.0.113.1,198.51.100.1,192.0.2.1"
```

Or via Railway Dashboard:
1. Go to staging environment
2. Variables tab
3. Add: `STAGING_ALLOWED_IPS` = `203.0.113.1,198.51.100.1,192.0.2.1`

### Step 3: Add Middleware (Same as Option 1 Step 3)

### Step 4: Deploy (Same as Option 1 Step 4)

### Step 5: Test It

```bash
# From allowed IP - should work
curl https://your-staging-domain/v1/models

# From different IP - should fail with 403
# (Test from different network/VPN)
```

✅ **Done!** Only allowed IPs can access staging.

---

## Option 3: Enable Both (Maximum Security)

Combine token authentication + IP whitelisting:

```bash
railway variables set STAGING_ACCESS_TOKEN="staging_abc123..."
railway variables set STAGING_ALLOWED_IPS="203.0.113.1,198.51.100.1"
```

Now requests must:
1. Come from an allowed IP
2. Include the correct token header

This provides the highest level of protection.

---

## Checking Current Security Status

### Via API

```bash
# Try to access without auth
curl -i https://your-staging-domain/v1/models
```

If security is enabled:
- **Status**: 403 Forbidden
- **Response**: Contains "Staging Access Denied"

If security is NOT enabled:
- **Status**: 200 OK
- **Response**: Returns model list

### Via Logs

```bash
# Check staging logs
railway logs --environment staging | grep "Staging security"

# Should see on startup:
# "Staging security enabled: Access Token"
# or
# "Staging security enabled: IP Whitelist (3 IPs)"
# or
# "Staging security not configured!"  (if not set up)
```

---

## Rotating Credentials

### When to Rotate
- Every 90 days (recommended)
- When a team member leaves
- If token is leaked
- After security incident

### How to Rotate

```bash
# 1. Generate new token
python3 -c "import secrets; print('staging_' + secrets.token_urlsafe(32))"

# 2. Update in Railway
railway environment switch staging
railway variables set STAGING_ACCESS_TOKEN="new-token-here"

# 3. Redeploy (automatic or manual)
railway up

# 4. Notify team of new token

# 5. Old token stops working immediately
```

---

## Troubleshooting

### Problem: Getting 403 even with correct token

**Check 1**: Verify header name is correct
```bash
# Correct (note the dash, not underscore)
-H "X-Staging-Access-Token: your-token"

# Wrong
-H "X_Staging_Access_Token: your-token"
```

**Check 2**: Verify token matches exactly (no extra spaces)
```bash
# Check what's set in Railway
railway variables | grep STAGING_ACCESS_TOKEN
```

**Check 3**: Check logs
```bash
railway logs --environment staging | tail -50
```

### Problem: Health check not working

Health checks should bypass security. If they don't work:

```bash
# Check the middleware code allows these paths
# ALLOWED_PATHS = {"/health", "/", "/ping", "/docs", "/redoc", "/openapi.json"}
```

### Problem: Can't access from my IP

If using IP whitelist:

```bash
# 1. Check your current IP
curl https://ifconfig.me

# 2. Check configured IPs
railway variables | grep STAGING_ALLOWED_IPS

# 3. Add your IP if missing
railway variables set STAGING_ALLOWED_IPS="existing-ips,your-new-ip"
```

### Problem: Security not working at all

**Check 1**: Verify middleware is added to main.py
```bash
grep "StagingSecurityMiddleware" src/main.py
```

**Check 2**: Verify environment is staging
```bash
railway variables | grep APP_ENV
# Should show: APP_ENV=staging
```

**Check 3**: Redeploy
```bash
railway up
```

---

## Monitoring Unauthorized Access

Check logs for unauthorized access attempts:

```bash
# View recent access denials
railway logs --environment staging | grep "Staging access denied"

# Example output:
# Staging access denied: IP not whitelisted (IP: 198.51.100.99)
# Staging access denied: Missing access token (IP: 203.0.113.50)
# Staging access denied: Invalid access token (IP: 192.0.2.10)
```

Set up alerts in Sentry for repeated unauthorized attempts.

---

## Quick Reference

### Required Environment Variables

```bash
# For token authentication
STAGING_ACCESS_TOKEN=staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1

# For IP whitelisting
STAGING_ALLOWED_IPS=203.0.113.1,198.51.100.1,192.0.2.1

# Can use both for maximum security
```

### Test Commands

```bash
# Test without auth (should fail)
curl https://your-staging-domain/v1/models

# Test with auth (should work)
curl https://your-staging-domain/v1/models \
  -H "X-Staging-Access-Token: your-token"

# Test health (should always work)
curl https://your-staging-domain/health
```

### Paths That Bypass Security

The following paths always work (no authentication required):
- `/health`
- `/ping`
- `/` (root)
- `/docs` (Swagger UI)
- `/redoc` (ReDoc)
- `/openapi.json`

---

## Summary

1. Generate token: `python3 -c "import secrets; print('staging_' + secrets.token_urlsafe(32))"`
2. Set in Railway: `railway variables set STAGING_ACCESS_TOKEN="your-token"`
3. Add middleware to `src/main.py`
4. Deploy: `git push origin main`
5. Test: `curl -H "X-Staging-Access-Token: your-token" https://your-staging-domain/v1/models`
6. Share token with team

**Time to set up:** ~5 minutes

**Security level:** 🔒 High (prevents unauthorized access)

---

**Need help?** See [STAGING_API_SECURITY.md](./STAGING_API_SECURITY.md) for detailed security options and troubleshooting.
