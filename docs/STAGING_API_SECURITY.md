# Staging API Security Guide

This guide shows you how to prevent unauthorized access to your staging/test API environment.

## Why Secure Staging?

- **Prevent abuse**: Stop random people from using your staging API
- **Protect resources**: Staging uses real provider API keys (costs money!)
- **Data privacy**: Test data should only be accessed by your team
- **Rate limit protection**: Prevent staging from being overwhelmed

## Security Options

We'll implement multiple layers of security (you can use one or combine several):

1. **IP Whitelisting** (Railway feature) - Easiest
2. **Custom Authentication Header** - Good for API testing
3. **Basic Authentication** - Simple but effective
4. **Domain Restriction** - Limit CORS origins
5. **Rate Limiting** - Prevent abuse

---

## Option 1: IP Whitelisting (Recommended - Easiest)

Railway can restrict access to your staging API to specific IP addresses.

### Step 1: Get Your Team's IP Addresses

```bash
# Get your current IP
curl https://ifconfig.me

# Or
curl https://api.ipify.org
```

Ask your team members to do the same.

### Step 2: Configure in Railway

**Via Railway Dashboard:**

1. Go to https://railway.app/
2. Select your project
3. Make sure **staging** environment is selected
4. Click on your service
5. Go to **"Settings"** tab
6. Scroll to **"Networking"** section
7. Under **"TCP Proxy"**, enable it
8. Under **"Allowed IPs"**, click **"+ Add IP"**
9. Add each IP address:
   - Your office IP
   - Your home IP
   - Team members' IPs
   - CI/CD runner IPs (if testing from GitHub Actions)
10. Click **"Save"**

**Example IPs to add:**
```
203.0.113.1      # Office
198.51.100.1     # Your home
192.0.2.1        # Team member 1
185.199.108.0/24 # GitHub Actions runners (if needed)
```

✅ **Result**: Only requests from these IPs can access staging

**Pros:**
- Very secure
- No code changes needed
- Railway handles it

**Cons:**
- Requires static IPs
- Difficult if team works remotely with dynamic IPs
- Need to update when IPs change

---

## Option 2: Custom Authentication Header

Add a secret token that must be included in all staging API requests.

### Step 1: Generate Staging Access Token

```bash
# Generate a random token
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

Or via Railway Dashboard:
1. Go to your service in staging environment
2. Variables tab
3. Add: `STAGING_ACCESS_TOKEN` = `your_generated_token`

### Step 3: Create Middleware

Create a new file: `src/middleware/staging_auth.py`

```python
"""Staging environment authentication middleware."""
import os
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config.config import Config


class StagingAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to protect staging environment with access token."""

    async def dispatch(self, request: Request, call_next):
        # Only apply in staging environment
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip auth for health check endpoint
        if request.url.path in ["/health", "/", "/ping"]:
            return await call_next(request)

        # Get the staging access token from environment
        staging_token = os.getenv("STAGING_ACCESS_TOKEN")

        if not staging_token:
            # If token not set, allow all (backward compatible)
            return await call_next(request)

        # Check for the token in headers
        auth_header = request.headers.get("X-Staging-Access-Token")

        if not auth_header or auth_header != staging_token:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "Staging Access Denied",
                    "message": "This is a staging/test environment. Valid X-Staging-Access-Token header required.",
                    "hint": "Contact your team admin for the staging access token."
                }
            )

        # Token is valid, proceed
        return await call_next(request)
```

### Step 4: Add Middleware to App

Edit `src/main.py`:

```python
from src.middleware.staging_auth import StagingAuthMiddleware

def create_app() -> FastAPI:
    app = FastAPI(...)

    # ... existing middleware ...

    # Add staging auth middleware (add near the top of middleware stack)
    app.add_middleware(StagingAuthMiddleware)

    # ... rest of your code ...

    return app
```

### Step 5: Test It

```bash
# Without token - should fail
curl https://your-staging-domain/v1/models

# With token - should work
curl https://your-staging-domain/v1/models \
  -H "X-Staging-Access-Token: staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1"
```

### Step 6: Share Token with Team

Create a secure document or use a password manager:

```
Staging API Access Token:
staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1

Usage:
curl https://staging.gatewayz.ai/v1/chat/completions \
  -H "X-Staging-Access-Token: staging_vq3K8xY9mL2nP5wR7tU4jH6gF8dS9aZ1" \
  -H "Authorization: Bearer gw_test_pro_key_12345" \
  ...
```

✅ **Result**: All API requests must include the staging token

**Pros:**
- Works from any IP
- Easy to rotate token
- Good for remote teams

**Cons:**
- Requires code changes
- Token could leak
- Need to include in all API calls

---

## Option 3: Basic Authentication

Add username/password to access staging API.

### Step 1: Generate Credentials

```bash
# Generate a random password
python3 -c "import secrets; print(secrets.token_urlsafe(16))"
```

Example:
- Username: `gatewayz-staging`
- Password: `pK9mL3nR7wT2xY5z`

### Step 2: Set in Railway

```bash
railway variables set STAGING_BASIC_AUTH_USERNAME="gatewayz-staging"
railway variables set STAGING_BASIC_AUTH_PASSWORD="pK9mL3nR7wT2xY5z"
```

### Step 3: Create Middleware

Create `src/middleware/staging_basic_auth.py`:

```python
"""Basic authentication for staging environment."""
import os
import base64
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.config.config import Config


class StagingBasicAuthMiddleware(BaseHTTPMiddleware):
    """Basic authentication middleware for staging."""

    async def dispatch(self, request: Request, call_next):
        # Only apply in staging
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip for health checks
        if request.url.path in ["/health", "/", "/ping"]:
            return await call_next(request)

        # Get credentials from environment
        username = os.getenv("STAGING_BASIC_AUTH_USERNAME")
        password = os.getenv("STAGING_BASIC_AUTH_PASSWORD")

        if not username or not password:
            # Auth not configured, allow through
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Basic "):
            return self._unauthorized_response()

        # Decode credentials
        try:
            encoded_credentials = auth_header.replace("Basic ", "")
            decoded = base64.b64decode(encoded_credentials).decode("utf-8")
            provided_username, provided_password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return self._unauthorized_response()

        # Verify credentials
        if provided_username != username or provided_password != password:
            return self._unauthorized_response()

        # Credentials valid
        return await call_next(request)

    def _unauthorized_response(self) -> Response:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "Authentication Required",
                "message": "This is a staging environment. Please provide valid credentials."
            },
            headers={"WWW-Authenticate": "Basic realm='Staging API'"}
        )
```

### Step 4: Add to App

Edit `src/main.py`:

```python
from src.middleware.staging_basic_auth import StagingBasicAuthMiddleware

def create_app() -> FastAPI:
    app = FastAPI(...)

    # Add basic auth middleware
    app.add_middleware(StagingBasicAuthMiddleware)

    return app
```

### Step 5: Test It

```bash
# Without auth - should fail
curl https://your-staging-domain/v1/models

# With basic auth - should work
curl https://your-staging-domain/v1/models \
  -u gatewayz-staging:pK9mL3nR7wT2xY5z

# Or with explicit header
curl https://your-staging-domain/v1/models \
  -H "Authorization: Basic Z2F0ZXdheXotc3RhZ2luZzpwSzltTDNuUjd3VDJ4WTV6"
```

✅ **Result**: Basic authentication protects staging

**Pros:**
- Standard HTTP authentication
- Browsers prompt for credentials
- Easy to understand

**Cons:**
- Need to include in all requests
- Less secure than tokens
- Credentials could leak

---

## Option 4: Domain/Origin Restriction

Limit which domains can make requests to staging (CORS).

### Step 1: Configure CORS for Staging

Edit `src/main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware
from src.config.config import Config

def create_app() -> FastAPI:
    app = FastAPI(...)

    # Configure CORS based on environment
    if Config.APP_ENV == "staging":
        # Strict CORS for staging - only allow your team's domains
        allowed_origins = [
            "https://staging-frontend.gatewayz.ai",
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    else:
        # Production CORS
        allowed_origins = [
            "https://gatewayz.ai",
            "https://beta.gatewayz.ai",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app
```

✅ **Result**: Only allowed origins can make browser requests

**Pros:**
- Prevents browser-based abuse
- Standard web security

**Cons:**
- Only protects against browser requests
- Can be bypassed with curl/scripts
- Not sufficient alone

---

## Option 5: Stricter Rate Limiting

Reduce rate limits for staging environment.

### Edit Rate Limiting Config

Edit `src/services/rate_limiting.py`:

```python
from src.config.config import Config

def get_rate_limit_config():
    """Get rate limit configuration based on environment."""

    if Config.APP_ENV == "staging":
        # Stricter limits for staging
        return {
            "requests_per_minute": 10,  # vs 60 in production
            "requests_per_hour": 100,   # vs 1000 in production
            "burst_size": 5,            # vs 20 in production
        }
    else:
        # Production limits
        return {
            "requests_per_minute": 60,
            "requests_per_hour": 1000,
            "burst_size": 20,
        }
```

✅ **Result**: Staging has lower rate limits

---

## Recommended Security Stack

For best protection, combine multiple methods:

### Level 1: Basic Protection (Choose One)
- **IP Whitelisting** (if team has static IPs)
- **OR Custom Auth Header** (if team is remote)

### Level 2: Additional Protection
- **Domain Restriction** (CORS)
- **Stricter Rate Limiting**

### Level 3: Monitoring
- **Alert on unusual activity**
- **Monitor staging usage in Grafana**
- **Check Sentry for unauthorized access attempts**

---

## Implementation Example: Combined Security

Here's how to implement both IP whitelisting + custom header:

### 1. Set Environment Variables

```bash
railway variables set STAGING_ACCESS_TOKEN="your-token"
railway variables set STAGING_ALLOWED_IPS="203.0.113.1,198.51.100.1"
```

### 2. Create Combined Middleware

`src/middleware/staging_security.py`:

```python
"""Combined staging security middleware."""
import os
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.config.config import Config


class StagingSecurityMiddleware(BaseHTTPMiddleware):
    """Combined security for staging environment."""

    async def dispatch(self, request: Request, call_next):
        # Only apply in staging
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip health checks
        if request.url.path in ["/health", "/", "/ping"]:
            return await call_next(request)

        # Check 1: IP Whitelist (if configured)
        allowed_ips = os.getenv("STAGING_ALLOWED_IPS", "").split(",")
        if allowed_ips and allowed_ips[0]:  # If list is not empty
            client_ip = request.client.host
            if client_ip not in allowed_ips:
                return self._access_denied("IP not allowed")

        # Check 2: Access Token (if configured)
        staging_token = os.getenv("STAGING_ACCESS_TOKEN")
        if staging_token:
            auth_header = request.headers.get("X-Staging-Access-Token")
            if not auth_header or auth_header != staging_token:
                return self._access_denied("Invalid access token")

        # All checks passed
        return await call_next(request)

    def _access_denied(self, reason: str) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": "Staging Access Denied",
                "message": f"Access denied: {reason}",
                "environment": "staging",
                "hint": "This is a test environment. Contact your team admin for access."
            }
        )
```

### 3. Add to App

```python
from src.middleware.staging_security import StagingSecurityMiddleware

app.add_middleware(StagingSecurityMiddleware)
```

---

## Testing Security

### Test 1: Unauthorized Access (Should Fail)

```bash
# Should return 403 Forbidden
curl https://your-staging-domain/v1/models
```

### Test 2: Authorized Access (Should Work)

```bash
# With proper token
curl https://your-staging-domain/v1/models \
  -H "X-Staging-Access-Token: your-token"
```

### Test 3: Health Check (Should Always Work)

```bash
# Health check should bypass security
curl https://your-staging-domain/health
```

---

## Rotating Security Credentials

### When to Rotate
- Every 90 days (recommended)
- When team member leaves
- If credentials are leaked
- After security incident

### How to Rotate

```bash
# 1. Generate new token
python3 -c "import secrets; print('staging_' + secrets.token_urlsafe(32))"

# 2. Update in Railway
railway environment switch staging
railway variables set STAGING_ACCESS_TOKEN="new-token"

# 3. Notify team
# Send new token to team via secure channel

# 4. Update CI/CD
# Update GitHub secrets if used in workflows

# 5. Old token stops working immediately after deploy
```

---

## Monitoring & Alerts

### Track Unauthorized Access Attempts

Add logging to your middleware:

```python
import logging

logger = logging.getLogger(__name__)

def _access_denied(self, reason: str) -> JSONResponse:
    # Log the attempt
    logger.warning(
        f"Staging access denied: {reason}",
        extra={
            "ip": request.client.host,
            "path": request.url.path,
            "user_agent": request.headers.get("user-agent"),
        }
    )

    # Send to Sentry
    sentry_sdk.capture_message(
        f"Unauthorized staging access attempt: {reason}",
        level="warning"
    )

    return JSONResponse(...)
```

### Set Up Alerts

In Sentry:
1. Go to Alerts
2. Create new alert
3. Condition: "Unauthorized staging access attempt"
4. Notify: Your team's Slack channel

---

## Quick Reference

### Security Options Summary

| Method | Security Level | Ease of Use | Works From Any IP | Code Changes |
|--------|----------------|-------------|-------------------|--------------|
| IP Whitelist | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ❌ | None |
| Custom Header | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ | Minimal |
| Basic Auth | ⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ | Minimal |
| CORS | ⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ | Minimal |
| Rate Limit | ⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ | Minimal |

**Recommended**: IP Whitelist + Custom Header + CORS + Rate Limiting

---

## FAQ

**Q: Which method should I use?**
A: If your team has static IPs, use IP whitelisting. Otherwise, use custom authentication header.

**Q: Can I use multiple methods?**
A: Yes! Combining methods provides better security.

**Q: What if someone gets the staging token?**
A: Rotate it immediately using the steps in "Rotating Security Credentials".

**Q: Should staging have the same security as production?**
A: No, staging needs different security focused on preventing unauthorized access, not authentication.

**Q: Do I need to secure health check endpoints?**
A: No, health checks should always be accessible for monitoring.

---

## Next Steps

1. Choose your security method(s)
2. Implement the middleware
3. Test thoroughly
4. Document for your team
5. Set up monitoring/alerts
6. Plan rotation schedule

---

**Remember**: Staging uses real API keys and costs money. Secure it properly! 🔒
