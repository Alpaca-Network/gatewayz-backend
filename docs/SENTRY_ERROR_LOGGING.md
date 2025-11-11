# Sentry Error Logging & Monitoring

## Overview

The Gatewayz API uses **Sentry** for comprehensive error tracking, monitoring, and debugging. This document describes the implementation, configuration, and best practices for error logging with:

- **Release tracking** with Git SHA
- **Environment detection** (dev/staging/production)
- **User context enrichment** (user ID, email, credits, plan)
- **Custom error fingerprinting** for better grouping
- **Performance monitoring** and distributed tracing
- **Source map support** (for JavaScript/frontend errors)

---

## Architecture

### Components

1. **`src/services/sentry_service.py`** - Core Sentry integration service
2. **`src/config/config.py`** - Sentry configuration management
3. **`src/main.py`** - Sentry initialization and global exception handler
4. **`src/security/deps.py`** - User context enrichment on authentication
5. **`src/routes/chat.py`** - Request context in inference endpoints

### Data Flow

```
Request → Authentication (set user context) → Route Handler (set request context)
   ↓
Error occurs
   ↓
Global Exception Handler → Sentry SDK → Custom Fingerprinting → Sentry Server
   ↓
Error grouped by fingerprint with full context:
- Release SHA
- Environment (dev/staging/prod)
- User info (ID, email, credits, plan)
- Request info (endpoint, method, model, provider)
- Breadcrumbs (request history)
- Stack trace
```

---

## Configuration

### Environment Variables

Add these to your `.env` file or deployment environment:

```bash
# Required
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
SENTRY_ENABLED=true

# Optional - Performance Monitoring
SENTRY_TRACES_SAMPLE_RATE=0.1      # 10% of transactions (adjust based on volume)
SENTRY_PROFILES_SAMPLE_RATE=0.1    # 10% profiling (adjust based on volume)

# Automatic Release Tracking (set by deployment platform)
RELEASE_SHA=<git-commit-sha>        # Auto-set by CI/CD
VERCEL_GIT_COMMIT_SHA=<sha>         # Auto-set by Vercel
RAILWAY_GIT_COMMIT_SHA=<sha>        # Auto-set by Railway

# Service Configuration
SERVICE_NAME=gatewayz-api           # Service identifier
APP_ENV=production                  # Environment: development/staging/production
```

### Deployment Platform Configuration

#### Vercel
Vercel automatically injects `VERCEL_GIT_COMMIT_SHA` - no additional configuration needed.

#### Railway
Railway automatically injects `RAILWAY_GIT_COMMIT_SHA` - no additional configuration needed.

#### Docker/Self-hosted
Pass `RELEASE_SHA` as an environment variable:

```bash
docker run -e RELEASE_SHA=$(git rev-parse HEAD) ...
```

#### GitHub Actions
The CI workflow automatically sets `RELEASE_SHA`:

```yaml
env:
  RELEASE_SHA: ${{ github.sha }}
```

---

## Features

### 1. Release Tracking

Every error is tagged with the **Git commit SHA** for precise version tracking:

```python
# Auto-detected from environment
Config.RELEASE_SHA  # Uses RELEASE_SHA, VERCEL_GIT_COMMIT_SHA, or RAILWAY_GIT_COMMIT_SHA
```

Release format in Sentry:
```
gatewayz-api@4322aec1234567890abcdef
```

This enables:
- Tracking which deploy introduced an error
- Comparing error rates across releases
- Triggering alerts on new release errors

### 2. Environment Detection

Errors are automatically tagged with the environment:

```python
Config.APP_ENV  # development, staging, production
```

### 3. User Context Enrichment

When a user authenticates, their context is automatically captured:

```python
# In src/security/deps.py (automatic on auth)
SentryService.set_user_context(
    user_id=str(user["id"]),
    email=user.get("email"),
    api_key_id=str(user.get("key_id", 0)),
    credits=user.get("credits"),
    plan=user.get("plan")
)
```

Visible in Sentry as:
- User ID
- Email
- API Key ID
- Current credits
- Subscription plan

### 4. Request Context

Request-specific information is captured for every error:

```python
# In route handlers
SentryService.set_request_context(
    endpoint="/v1/chat/completions",
    method="POST",
    model="gpt-4",
    provider="openrouter",
    stream=True
)
```

### 5. Custom Error Fingerprinting

Errors are intelligently grouped using custom fingerprints:

#### Fingerprint Strategy

```python
# Base fingerprint: exception type
fingerprint = [exc_type]

# Add specific patterns:
if "InsufficientCredits" in exc_type:
    fingerprint.append("insufficient_credits")
elif "RateLimitExceeded" in exc_type:
    fingerprint.append("rate_limit")
elif "ProviderError" in exc_type or "APIError" in exc_type:
    fingerprint.append("provider_error")
elif "AuthenticationError" in exc_type:
    fingerprint.append("auth_error")
elif "ValidationError" in exc_type:
    fingerprint.append("validation_error")
else:
    # Use first line of error message
    fingerprint.append(first_line_of_message)
```

#### Benefits
- ✅ Identical errors are grouped together (not split by user/request)
- ✅ Similar errors with different messages are grouped
- ✅ Easier to identify systemic issues vs. one-off errors
- ✅ Better signal-to-noise ratio in alerts

### 6. User Impact Tagging

Errors are automatically tagged with **user impact level**:

```python
# High impact: Core inference and auth endpoints
if "/chat/completions" in url or "/messages" in url:
    user_impact = "high"
elif "/auth" in url or "/api_keys" in url:
    user_impact = "high"
# Medium impact: User management
elif "/users" in url or "/payments" in url:
    user_impact = "medium"
# Low impact: Everything else
else:
    user_impact = "low"
```

Use this to:
- Prioritize high-impact errors
- Set up separate alerts for critical paths
- Track user-facing vs. internal errors

### 7. Breadcrumbs

Breadcrumbs provide a timeline of events leading to an error:

```python
from src.services.sentry_service import add_breadcrumb

add_breadcrumb(
    message="Chat completion request started",
    category="inference",
    level="info",
    data={"model": "gpt-4", "stream": True}
)
```

Categories:
- `auth` - Authentication events
- `db` - Database operations
- `inference` - AI model requests
- `provider` - Provider API calls
- `cache` - Cache operations
- `rate_limit` - Rate limiting checks

### 8. Performance Monitoring

Sentry automatically tracks:
- Request duration
- Database query time
- HTTP request latency
- Provider API response times

Configure sample rate to balance cost vs. visibility:

```bash
SENTRY_TRACES_SAMPLE_RATE=0.1  # Track 10% of requests
```

---

## Usage

### Automatic Error Capture

Most errors are automatically captured via:

1. **Global Exception Handler** (`src/main.py:253`)
   - Catches all unhandled exceptions
   - Adds request context
   - Captures in Sentry

2. **FastAPI Integration**
   - Automatically instruments all endpoints
   - Tracks request/response
   - Captures HTTP errors

3. **HTTPX Integration**
   - Tracks all outbound HTTP requests (provider calls)
   - Captures timeouts and connection errors

### Manual Error Capture

For specific error scenarios:

```python
from src.services.sentry_service import SentryService

try:
    # Risky operation
    result = make_provider_call()
except Exception as e:
    SentryService.capture_exception(
        e,
        context={"provider": "openrouter", "model": "gpt-4"},
        tags={"error_type": "provider_timeout"},
        level="error"
    )
    raise
```

### Decorator for Error Tracking

Use the `@capture_errors` decorator for automatic error capture with context:

```python
from src.services.sentry_service import capture_errors

@capture_errors(operation="process_payment", capture_args=True)
async def process_payment(user_id: str, amount: float):
    # Function implementation
    pass
```

Benefits:
- Automatically captures exceptions
- Adds operation context
- Optionally captures function arguments (careful with sensitive data!)

### Adding Custom Tags

```python
from src.services.sentry_service import SentryService

# Add custom tags for filtering
SentryService.capture_exception(
    error,
    tags={
        "custom_tag": "value",
        "category": "payment_error"
    }
)
```

---

## Best Practices

### 1. Don't Log Sensitive Data

The service is configured with `send_default_pii=False` and automatically redacts:
- API keys
- Passwords
- Tokens
- Authorization headers

**Always** be careful when adding custom context or tags.

### 2. Use Structured Context

Instead of:
```python
# ❌ Bad
SentryService.capture_message(f"User {user_id} failed payment")
```

Do:
```python
# ✅ Good
SentryService.capture_exception(
    error,
    context={"user_id": user_id, "payment_method": "stripe"},
    tags={"error_category": "payment"}
)
```

### 3. Set Appropriate Sample Rates

**High-traffic production:**
```bash
SENTRY_TRACES_SAMPLE_RATE=0.01  # 1% sampling
SENTRY_PROFILES_SAMPLE_RATE=0.01
```

**Staging/development:**
```bash
SENTRY_TRACES_SAMPLE_RATE=1.0   # 100% sampling
SENTRY_PROFILES_SAMPLE_RATE=0.5  # 50% profiling
```

### 4. Use Breadcrumbs Liberally

Breadcrumbs are lightweight and extremely valuable for debugging:

```python
add_breadcrumb("Starting provider request", category="provider", level="info")
add_breadcrumb("Database query completed", category="db", level="debug")
add_breadcrumb("Rate limit check passed", category="rate_limit", level="info")
```

### 5. Test Error Capture

Test Sentry integration:

```python
# Trigger test error
from src.services.sentry_service import SentryService
SentryService.capture_message("Test error from Gatewayz API", level="info")
```

Check Sentry dashboard to verify:
- Error appears
- Release SHA is correct
- Environment is correct
- Tags are present

---

## Error Fingerprint Examples

### Example 1: Insufficient Credits

```
Error: InsufficientCreditsError: User has 0 credits
Fingerprint: ["InsufficientCreditsError", "insufficient_credits"]
```

All "insufficient credits" errors group together, regardless of user or specific message.

### Example 2: Provider Timeout

```
Error: ProviderAPIError: OpenRouter API timeout after 30s
Fingerprint: ["ProviderAPIError", "provider_error"]
```

All provider errors group together.

### Example 3: Validation Error

```
Error: ValidationError: Invalid model parameter 'gpt-999'
Fingerprint: ["ValidationError", "validation_error"]
```

All validation errors group together.

### Example 4: Unique Errors

```
Error: ValueError: Unexpected format in response
Fingerprint: ["ValueError", "Unexpected format in response"]
```

Unique errors use first line of message for grouping.

---

## Sentry Dashboard

### Key Views

1. **Issues** - Grouped errors with fingerprints
2. **Performance** - Request/response times, database queries
3. **Releases** - Track errors by Git SHA
4. **Alerts** - Configure notifications for critical errors

### Recommended Alerts

1. **High Impact Errors**
   ```
   Filter: user_impact = high
   Threshold: > 10 events in 5 minutes
   ```

2. **New Release Errors**
   ```
   Filter: release = latest
   Threshold: > 5 new issues in 1 hour
   ```

3. **Provider Errors**
   ```
   Filter: error_type = provider_error
   Threshold: > 50 events in 10 minutes
   ```

---

## Source Maps (JavaScript Errors)

For frontend errors, upload source maps to Sentry:

### Vercel Deployment

Add to `vercel.json`:

```json
{
  "build": {
    "env": {
      "SENTRY_AUTH_TOKEN": "@sentry-auth-token",
      "SENTRY_ORG": "your-org",
      "SENTRY_PROJECT": "gatewayz-api"
    }
  }
}
```

Install Sentry CLI:

```bash
npm install @sentry/cli --save-dev
```

Add to `package.json`:

```json
{
  "scripts": {
    "build": "next build && sentry-cli sourcemaps upload --release=$VERCEL_GIT_COMMIT_SHA ./out"
  }
}
```

---

## Troubleshooting

### Errors Not Appearing in Sentry

1. **Check DSN configuration:**
   ```bash
   echo $SENTRY_DSN
   ```

2. **Verify Sentry is enabled:**
   ```bash
   echo $SENTRY_ENABLED  # Should be "true"
   ```

3. **Check initialization logs:**
   ```
   Look for: "Sentry initialized successfully"
   ```

4. **Test manually:**
   ```python
   from src.services.sentry_service import SentryService
   SentryService.capture_message("Test", level="info")
   ```

### Release SHA Not Showing

1. **Check environment variable:**
   ```bash
   echo $RELEASE_SHA
   # or
   echo $VERCEL_GIT_COMMIT_SHA
   # or
   echo $RAILWAY_GIT_COMMIT_SHA
   ```

2. **Verify in config:**
   ```python
   from src.config import Config
   print(Config.RELEASE_SHA)  # Should not be "unknown"
   ```

### High Event Volume

If you're exceeding Sentry quota:

1. **Lower sample rates:**
   ```bash
   SENTRY_TRACES_SAMPLE_RATE=0.01
   ```

2. **Filter noisy errors:**
   ```python
   # In sentry_service.py _before_send hook
   if "health check" in event.get("message", ""):
       return None  # Drop event
   ```

3. **Use Sentry's inbound filters** in dashboard

---

## Cost Optimization

### Sentry Pricing

Sentry charges based on:
- Number of errors captured
- Number of transactions (performance monitoring)
- Number of profiles
- Data retention

### Optimization Strategies

1. **Sample performance monitoring:**
   ```bash
   # Production: 1-10%
   SENTRY_TRACES_SAMPLE_RATE=0.05
   ```

2. **Filter expected errors:**
   - Don't capture 404s, validation errors in production
   - Filter health check endpoints

3. **Use different sample rates per environment:**
   ```python
   if Config.IS_PRODUCTION:
       traces_sample_rate = 0.01
   elif Config.IS_STAGING:
       traces_sample_rate = 0.1
   else:
       traces_sample_rate = 1.0
   ```

4. **Set rate limits in Sentry dashboard:**
   - Per-project event limits
   - Spike protection

---

## Integration with Other Services

### Prometheus Metrics

Sentry complements (doesn't replace) Prometheus:
- **Prometheus**: System metrics, request counts, latencies
- **Sentry**: Error details, stack traces, user context

### PostHog Analytics

Sentry errors can be correlated with PostHog events:
```python
# Link error to user session
SentryService.set_user_context(
    user_id=user_id,
    posthog_session_id=session_id
)
```

### Braintrust (ML Observability)

Braintrust tracks AI/ML performance; Sentry tracks errors:
- Use both for comprehensive observability
- Sentry captures provider errors; Braintrust tracks model quality

---

## Security Considerations

### Data Privacy

1. **PII Redaction**: Automatic redaction of sensitive fields
2. **Custom scrubbing**: Add custom patterns in `_before_send` hook
3. **User consent**: Consider GDPR implications of error tracking

### Access Control

1. **Sentry organization**: Limit access to authorized team members
2. **Rotate auth tokens**: Regular rotation of Sentry API tokens
3. **Audit logs**: Review Sentry access logs regularly

---

## Example: Full Error Tracking Flow

### 1. User Makes Request

```http
POST /v1/chat/completions
Authorization: Bearer sk-xxx...
```

### 2. Authentication (src/security/deps.py)

```python
# User context set automatically
SentryService.set_user_context(
    user_id="user_123",
    email="user@example.com",
    credits=1000,
    plan="pro"
)
```

### 3. Route Handler (src/routes/chat.py)

```python
# Request context set
SentryService.set_request_context(
    endpoint="/v1/chat/completions",
    method="POST",
    model="gpt-4",
    stream=False
)

add_breadcrumb("Chat request started", category="inference")
```

### 4. Error Occurs

```python
# Provider timeout
raise ProviderAPIError("OpenRouter timeout")
```

### 5. Global Exception Handler (src/main.py)

```python
# Captures with all context
SentryService.capture_exception(exc)
```

### 6. Sentry Event Created

```json
{
  "event_id": "abc123...",
  "release": "gatewayz-api@4322aec",
  "environment": "production",
  "user": {
    "id": "user_123",
    "email": "user@example.com",
    "credits": 1000,
    "plan": "pro"
  },
  "tags": {
    "service": "gatewayz-api",
    "model": "gpt-4",
    "user_impact": "high"
  },
  "contexts": {
    "request": {
      "endpoint": "/v1/chat/completions",
      "method": "POST",
      "model": "gpt-4"
    }
  },
  "fingerprint": ["ProviderAPIError", "provider_error"],
  "breadcrumbs": [
    {"message": "Chat request started", "category": "inference"}
  ]
}
```

### 7. Grouped in Sentry

All similar provider errors grouped together with stable fingerprint.

---

## Summary

The Sentry integration provides:

✅ **Release tracking** - Git SHA in every error
✅ **Environment tagging** - dev/staging/production
✅ **User context** - ID, email, credits, plan
✅ **Request context** - Endpoint, method, model, provider
✅ **Custom fingerprinting** - Intelligent error grouping
✅ **User impact tagging** - Prioritize critical errors
✅ **Performance monitoring** - Request/DB/provider timings
✅ **Breadcrumbs** - Event timeline for debugging
✅ **Security** - PII redaction, access controls

Configure once, capture everywhere. All errors automatically enriched with full context.

---

## Additional Resources

- [Sentry Python SDK Documentation](https://docs.sentry.io/platforms/python/)
- [Sentry FastAPI Integration](https://docs.sentry.io/platforms/python/guides/fastapi/)
- [Error Fingerprinting](https://docs.sentry.io/platform-redirect/?next=/data-management/event-grouping/)
- [Performance Monitoring](https://docs.sentry.io/product/performance/)
- [Source Maps](https://docs.sentry.io/platforms/javascript/sourcemaps/)

---

**Last Updated**: 2025-11-11
**Version**: 1.0.0
