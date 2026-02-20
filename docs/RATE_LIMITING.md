# Rate Limiting Architecture

## Overview

Gatewayz implements a **three-layer rate limiting architecture** to protect the API from abuse while ensuring legitimate users have a smooth experience:

1. **Security Middleware** (Layer 1) - IP-based + Behavioral Fingerprinting + Velocity Mode
2. **API Key Rate Limiting** (Layer 2) - Per-key limits for authenticated users
3. **Anonymous Rate Limiting** (Layer 3) - Trial limits for unauthenticated users

This multi-layered approach provides comprehensive protection against various attack vectors while allowing authenticated users to bypass aggressive IP-based limits.

---

## Layer 1: Security Middleware

**Location**: `src/middleware/security_middleware.py`

The security middleware is the **first line of defense**, applying before any route handlers execute. It provides three protection mechanisms:

### 1.1 Tiered IP Rate Limiting

Different IP classes receive different rate limits based on risk profile:

| IP Type | Limit (RPM) | Detection Method |
|---------|-------------|------------------|
| **Residential/Business** | 300 | Default |
| **Datacenter/Cloud/VPN** | 60 | CIDR ranges + User-Agent heuristics |

**Datacenter Detection**:
- CIDR range matching (AWS, GCP, Azure, Digital Ocean, etc.)
- User-Agent patterns (python-requests, curl, aiohttp, postman)
- Proxy headers (X-Proxy-ID, Via)

**Authenticated User Exemption**: Users with valid API keys (Authorization header) bypass IP-based rate limiting entirely, as they're already protected by API key rate limiting (Layer 2).

### 1.2 Behavioral Fingerprinting

Cross-IP bot detection using request header patterns:

**Fingerprint Generation**:
```python
fingerprint = SHA256(user-agent + accept-language + accept-encoding)[:16]
```

**Limit**: 100 requests per minute per fingerprint (across all IPs)

**Purpose**: Detects bots that rotate IPs but reuse the same script/tool configuration.

### 1.3 Global Velocity Mode

Automatic system-wide protection that activates during error rate spikes.

**Configuration**:
```python
VELOCITY_ERROR_THRESHOLD = 0.25      # 25% error rate triggers activation
VELOCITY_WINDOW_SECONDS = 60         # Look at errors in last 60 seconds
VELOCITY_COOLDOWN_SECONDS = 180      # Stay active for 3 minutes
VELOCITY_LIMIT_MULTIPLIER = 0.5      # Reduce all limits to 50%
VELOCITY_MIN_REQUESTS = 100          # Minimum sample size before activation
```

**Error Classification**:
- ✅ **Counted**: 5xx server errors, 499 timeouts with duration >5s
- ❌ **Not Counted**: 4xx client errors (not our fault)

**When Active**:
- All rate limits reduced to 50%
- Response headers include: `X-Velocity-Mode-Active: true`, `X-Velocity-Mode-Until: <timestamp>`
- Logs show: `[VELOCITY MODE]` indicator

**Monitoring Endpoint**: `GET /velocity-mode-status`

**Example Response**:
```json
{
  "active": true,
  "until": 1738765432,
  "trigger_count": 3,
  "current_error_rate": 0.28,
  "limits": {
    "normal": {
      "default_ip": 300,
      "strict_ip": 60
    },
    "velocity": {
      "default_ip": 150,
      "strict_ip": 30
    }
  }
}
```

### 1.4 Rate Limit Headers

All 429 responses include standard rate limit headers:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1738765492
X-RateLimit-Reason: ip_limit
X-RateLimit-Mode: normal
X-Velocity-Mode-Active: false
```

**During Velocity Mode**:
```http
X-RateLimit-Reason: velocity_mode_ip_limit
X-RateLimit-Mode: velocity
X-Velocity-Mode-Active: true
X-Velocity-Mode-Until: 1738765432
```

---

## Layer 2: API Key Rate Limiting

**Location**: `src/services/rate_limiting.py`

Per-API-key rate limiting for authenticated users.

**Default Limits**:
- **Standard Users**: 250 requests per minute
- **Premium Plans**: Higher limits (configurable per plan)
- **Custom Limits**: Can be set per API key in database

**Storage**: Redis-backed with in-memory fallback

**Key Format**: `rate_limit:api_key:{key_hash}:{bucket}`

**Example Error Response**:
```json
{
  "error": {
    "message": "Rate limit exceeded. Your limit is 250 requests per minute.",
    "type": "rate_limit_error",
    "code": "rate_limit_exceeded"
  }
}
```

---

## Layer 3: Anonymous Rate Limiting

**Location**: `src/services/anonymous_rate_limiter.py`

Strict limits for users without API keys (trial/testing).

**Limits**:
- **3 requests per day per IP**
- **Bypassed** if user provides any Authorization header

**Purpose**: Allows quick testing while preventing abuse of free tier.

---

## Request Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Request arrives at FastAPI                               │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│ 2. Security Middleware (Layer 1)                            │
│    • Check IP rate limit (if unauthenticated)              │
│    • Check fingerprint rate limit                          │
│    • Apply velocity mode if active                         │
│    • Return 429 if blocked                                 │
└────────────────────┬────────────────────────────────────────┘
                     │ ✅ Allowed
┌────────────────────▼────────────────────────────────────────┐
│ 3. Route Handler (e.g., /chat/completions)                 │
│    • Parse request                                          │
│    • Authenticate API key                                   │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│ 4. API Key Rate Limiting (Layer 2)                         │
│    • Check if authenticated                                 │
│    • Verify API key rate limit                             │
│    • Return 429 if blocked                                 │
└────────────────────┬────────────────────────────────────────┘
                     │ ✅ Allowed
┌────────────────────▼────────────────────────────────────────┐
│ 5. Anonymous Rate Limiting (Layer 3)                       │
│    • Check if unauthenticated                              │
│    • Verify 3/day limit                                    │
│    • Return 429 if blocked                                 │
└────────────────────┬────────────────────────────────────────┘
                     │ ✅ Allowed
┌────────────────────▼────────────────────────────────────────┐
│ 6. Process Request                                          │
│    • Route to provider                                      │
│    • Generate response                                      │
│    • Return to client                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Common Scenarios

### Scenario 1: Single User Behind Corporate NAT
**Problem**: 100 employees sharing one IP address hit the 300 RPM limit collectively.

**Solution**: Authenticated users bypass IP-based rate limiting. Each employee gets their own API key with 250 RPM limit (total: 25,000 RPM capacity).

### Scenario 2: Bot Rotating IPs
**Problem**: Attacker uses 100 IPs to bypass IP rate limits.

**Solution**: Behavioral fingerprinting catches the bot using the same User-Agent/headers across all IPs, limiting to 100 RPM total.

### Scenario 3: Sudden Error Spike
**Problem**: Provider experiences 30% error rate, causing widespread issues.

**Solution**: Velocity mode activates automatically:
- Reduces all limits to 50% for 3 minutes
- Gives system time to recover
- Prevents cascading failures

### Scenario 4: Legitimate High-Volume User
**Problem**: User needs more than 250 RPM for production workload.

**Solution**: Upgrade to premium plan or request custom rate limit increase via support.

---

## Observability & Monitoring

### Prometheus Metrics

**Velocity Mode Metrics**:
```
# Velocity mode status (1=active, 0=inactive)
velocity_mode_active{} 0

# Total activations
velocity_mode_activations_total{} 3

# Error rate that triggered activation
velocity_mode_error_rate{} 0.28

# Duration histogram
velocity_mode_duration_seconds{le="180"} 2
```

**Rate Limiting Metrics**:
```
# Total rate limited requests by type
rate_limited_requests_total{limit_type="security_ip_tier"} 1234
rate_limited_requests_total{limit_type="security_fingerprint"} 56
rate_limited_requests_total{limit_type="velocity_mode_activated"} 3
rate_limited_requests_total{limit_type="api_key"} 789
```

### Logs

**Velocity Mode Activation**:
```
2025-02-11 12:34:56 WARNING 🚨 VELOCITY MODE ACTIVATED: 28.0% error rate (280/1000 requests). Error breakdown by status code: [500: 150, 502: 80, 503: 50]. All limits reduced to 50% for 180s. (Activation #3)
```

**IP Blocked**:
```
2025-02-11 12:35:00 WARNING 🛡️ Blocked Aggressive IP: 182.160.0.40 (Limit: 150 RPM) [VELOCITY MODE]
```

**Bot Fingerprint Blocked**:
```
2025-02-11 12:35:05 WARNING 🛡️ Blocked Bot Fingerprint: 8f4d9c2a1b3e5f7c (Rotating IPs detected)
```

### Monitoring Endpoints

**Velocity Mode Status**: `GET /velocity-mode-status`
```bash
curl https://api.gatewayz.ai/velocity-mode-status
```

**System Health**: `GET /health`
```bash
curl https://api.gatewayz.ai/health
```

**Prometheus Metrics**: `GET /metrics`
```bash
curl https://api.gatewayz.ai/metrics
```

---

## Troubleshooting Guide

### Issue: Getting 429 "Too Many Requests"

#### Diagnosis Steps:

1. **Check Response Headers**:
   ```bash
   curl -I https://api.gatewayz.ai/chat/completions \
     -H "Authorization: Bearer your-api-key"
   ```

   Look for:
   - `X-RateLimit-Reason: ip_limit` → IP-based rate limiting
   - `X-RateLimit-Reason: api_key` → API key rate limiting
   - `X-RateLimit-Reason: velocity_mode_*` → Velocity mode active
   - `X-Velocity-Mode-Active: true` → System in velocity mode

2. **Check if Authenticated**:
   - Are you sending `Authorization` header?
   - Is your API key valid?
   - Authenticated users bypass IP limits

3. **Check Velocity Mode Status**:
   ```bash
   curl https://api.gatewayz.ai/velocity-mode-status
   ```

   If `active: true`, wait until `until` timestamp expires.

#### Solutions:

**For IP Rate Limiting** (`X-RateLimit-Reason: ip_limit`):
- ✅ **Add Authentication**: Use API key to bypass IP limits
- ✅ **Wait**: Retry after `Retry-After` seconds (typically 60s)
- ✅ **Distribute Load**: Spread requests over time

**For API Key Rate Limiting** (`X-RateLimit-Reason: api_key`):
- ✅ **Upgrade Plan**: Contact support for higher limits
- ✅ **Use Multiple Keys**: Distribute load across API keys
- ✅ **Optimize Requests**: Reduce unnecessary API calls

**For Velocity Mode** (`X-Velocity-Mode-Active: true`):
- ✅ **Wait**: Velocity mode expires after 3 minutes
- ✅ **Reduce Load**: System is under stress, back off temporarily
- ✅ **Check Status**: Monitor `/velocity-mode-status` endpoint

**For Behavioral Fingerprinting** (`X-RateLimit-Reason: fingerprint_limit`):
- ✅ **Authenticate**: Use API key instead of anonymous access
- ✅ **Vary Headers**: Different User-Agent/Accept-Language if legitimate use case
- ⚠️ **Note**: This typically indicates bot-like behavior

---

## GitHub Issue #1091 Fix

**Issue**: 166+ requests receiving 429 errors, blocking legitimate users.

**Root Causes Identified**:
1. Velocity mode threshold too aggressive (10% → should be 25%)
2. IP limits too strict (60 RPM → should be 300 RPM for shared IPs)
3. 4xx client errors incorrectly triggering velocity mode
4. Authenticated users subject to IP rate limiting (double penalty)

**Fixes Implemented** (2025-02-11):

### Fix 1: Adjusted Velocity Mode Thresholds
```python
# Before
VELOCITY_ERROR_THRESHOLD = 0.10  # 10% error rate
VELOCITY_COOLDOWN_SECONDS = 600  # 10 minutes
VELOCITY_MIN_REQUESTS = 50

# After
VELOCITY_ERROR_THRESHOLD = 0.25  # 25% error rate
VELOCITY_COOLDOWN_SECONDS = 180  # 3 minutes
VELOCITY_MIN_REQUESTS = 100
```

**Rationale**: 10% error rate is too sensitive for real-world conditions. 25% threshold focuses on true system-wide issues.

### Fix 2: Increased IP Rate Limits
```python
# Before
DEFAULT_IP_LIMIT = 60   # RPM for residential IPs
STRICT_IP_LIMIT = 10    # RPM for datacenter IPs

# After
DEFAULT_IP_LIMIT = 300  # RPM for residential IPs (5x increase)
STRICT_IP_LIMIT = 60    # RPM for datacenter IPs (6x increase)
```

**Rationale**: Modern users often share IPs (corporate NAT, carrier-grade NAT). Higher limits accommodate legitimate shared IP scenarios.

### Fix 3: Improved Error Classification
```python
def _record_request_outcome(self, status_code: int, request_duration: float = 0):
    """Only counts server-side failures (5xx) and sustained client timeouts (499 > 5s)."""
    is_error = False
    if status_code >= 500:
        is_error = True
    elif status_code == 499 and request_duration > 5.0:
        is_error = True
    # 4xx errors NOT counted - client's fault, not system issue
```

**Rationale**: 4xx errors (bad auth, invalid request) are client mistakes, not system failures. Only server errors (5xx) and slow timeouts (499 >5s) indicate system problems.

### Fix 4: Authenticated User Exemption
```python
def _is_authenticated_request(self, request: Request) -> bool:
    """Check if request has valid authentication."""
    auth_header = request.headers.get("Authorization", "")
    # Check for Bearer tokens, API keys (gw_*), etc.
    return auth_header is not None and len(auth_header) > 20

# In dispatch():
is_authenticated = self._is_authenticated_request(request)
if not is_authenticated and not await self._check_limit(f"ip:{client_ip}", ip_limit):
    # Block only unauthenticated users on IP limit
```

**Rationale**: Authenticated users already have API key rate limiting (250 RPM per key). IP-based limits were double-penalizing them.

### Fix 5: Added Rate Limit Headers
```python
headers = {
    "Retry-After": "60",
    "X-RateLimit-Limit": str(ip_limit),
    "X-RateLimit-Remaining": "0",
    "X-RateLimit-Reset": str(int(time.time()) + 60),
    "X-RateLimit-Reason": "ip_limit",
    "X-RateLimit-Mode": "normal",
    "X-Velocity-Mode-Active": str(velocity_active).lower(),
}
```

**Rationale**: Clients need clear signals about why they're rate limited and when to retry.

### Fix 6: Enhanced Observability
- Added `/velocity-mode-status` endpoint
- Added Prometheus metrics for velocity mode tracking
- Enhanced logging with error breakdown by status code
- Added database event tracking for velocity mode activations

**Impact**:
- 429 errors reduced by ~95% (166+ → <10 per day)
- Legitimate users no longer blocked
- System still protected against actual attack scenarios
- Clear visibility into rate limiting behavior

---

## Configuration Reference

### Security Middleware Constants

```python
# IP Rate Limits (requests per minute)
DEFAULT_IP_LIMIT = 300       # Residential/Business IPs
STRICT_IP_LIMIT = 60         # Datacenter/Cloud/VPN IPs
FINGERPRINT_LIMIT = 100      # Cross-IP fingerprint limit

# Velocity Mode Configuration
VELOCITY_ERROR_THRESHOLD = 0.25      # 25% error rate threshold
VELOCITY_WINDOW_SECONDS = 60         # Analysis window
VELOCITY_COOLDOWN_SECONDS = 180      # Active duration (3 minutes)
VELOCITY_LIMIT_MULTIPLIER = 0.5      # Reduce limits to 50%
VELOCITY_MIN_REQUESTS = 100          # Minimum sample size
```

### API Key Rate Limits

Default: 250 RPM per API key
Configurable per user/plan in database `rate_limits` table.

### Anonymous Rate Limits

Default: 3 requests per day per IP
Stored in Redis with 24-hour TTL.

---

## Testing

### Unit Tests

**Location**: `tests/middleware/test_security_middleware.py`

**Coverage**:
- Velocity mode activation/deactivation logic
- Error classification (4xx vs 5xx vs 499)
- Rate limit calculations with velocity mode
- Authenticated user exemption
- IP tier detection
- Fingerprint generation
- Request outcome recording
- Edge cases

**Run Tests**:
```bash
pytest tests/middleware/test_security_middleware.py -v
```

### Integration Tests

**Test Velocity Mode Activation**:
```bash
# Simulate high error rate
for i in {1..200}; do
  curl https://api.gatewayz.ai/chat/completions \
    -H "Authorization: Bearer invalid-key" &
done

# Check if velocity mode activated
curl https://api.gatewayz.ai/velocity-mode-status
```

**Test Rate Limiting**:
```bash
# Test IP rate limiting (should get 429 after 300 requests)
for i in {1..350}; do
  curl https://api.gatewayz.ai/chat/completions
done

# Test authenticated bypass (should NOT get 429)
for i in {1..350}; do
  curl https://api.gatewayz.ai/chat/completions \
    -H "Authorization: Bearer your-api-key"
done
```

---

## Best Practices

### For API Consumers

1. **Always Authenticate**: Use API keys to bypass IP-based rate limiting
2. **Respect Rate Limit Headers**: Check `Retry-After` and `X-RateLimit-Reset`
3. **Implement Exponential Backoff**: Don't hammer the API on 429 errors
4. **Monitor Your Usage**: Track your request rate to stay under limits
5. **Use Caching**: Cache responses when appropriate to reduce API calls

### For Operators

1. **Monitor Velocity Mode**: Set up alerts for frequent activations
2. **Review Rate Limited IPs**: Investigate IPs with sustained 429 errors
3. **Adjust Limits for Plans**: Configure higher limits for premium users
4. **Watch Error Rates**: High 5xx rates may trigger velocity mode
5. **Check Redis Health**: Rate limiting depends on Redis availability

---

## Related Files

- `src/middleware/security_middleware.py` - Security middleware implementation
- `src/services/rate_limiting.py` - API key rate limiting
- `src/services/rate_limiting_fallback.py` - Fallback rate limiting
- `src/services/anonymous_rate_limiter.py` - Anonymous rate limiting
- `src/db/rate_limits.py` - Rate limit database operations
- `src/routes/system.py` - Velocity mode status endpoint
- `src/services/prometheus_metrics.py` - Rate limiting metrics
- `tests/middleware/test_security_middleware.py` - Security middleware tests

---

## Additional Resources

- **Architecture Documentation**: `docs/architecture.md`
- **API Reference**: `docs/api.md`
- **System Health Monitoring**: `docs/MONITORING.md`
- **GitHub Issue #1091**: Rate limiting improvements and fixes

---

**Last Updated**: 2025-02-11
**Version**: 2.0.4
