# Error Logging Setup - Quick Start

## 5-Minute Setup

### 1. Create Sentry Account

1. Go to [sentry.io](https://sentry.io)
2. Sign up (free tier available)
3. Create new project → Select "Python" → Select "FastAPI"
4. Copy your DSN (looks like: `https://xxx@yyy.ingest.sentry.io/zzz`)

### 2. Add Environment Variables

Add to `.env` (local) or deployment settings (production):

```bash
SENTRY_DSN=https://your-dsn-here@sentry.io/project-id
SENTRY_ENABLED=true

# Optional: Adjust sampling rates
SENTRY_TRACES_SAMPLE_RATE=0.1        # 10% of transactions (lower in high-traffic prod)
SENTRY_PROFILES_SAMPLE_RATE=0.1      # 10% profiling

# Release tracking (auto-set by most platforms)
RELEASE_SHA=<will-be-auto-detected>
```

### 3. Install Dependencies

Already included in `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 4. Verify Setup

Start the application:

```bash
python src/main.py
```

Look for log message:
```
✅ Sentry initialized successfully: service=gatewayz-api, env=development, release=<sha>
```

### 5. Test Error Capture

**Method 1: Trigger test error**

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-invalid-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]}'
```

**Method 2: Python test**

```python
from src.services.sentry_service import SentryService
SentryService.capture_message("Test error from Gatewayz API", level="info")
```

**Method 3: Raise exception**

Add temporary code:
```python
raise Exception("Test Sentry integration")
```

### 6. Check Sentry Dashboard

1. Go to Sentry dashboard
2. Click "Issues"
3. You should see your test error with:
   - ✅ Release SHA
   - ✅ Environment tag
   - ✅ Stack trace
   - ✅ Request context

---

## Deployment Platform Setup

### Vercel

1. Add to Vercel environment variables:
   ```
   SENTRY_DSN=<your-dsn>
   SENTRY_ENABLED=true
   ```

2. `VERCEL_GIT_COMMIT_SHA` is automatically set ✅

### Railway

1. Add to Railway environment variables:
   ```
   SENTRY_DSN=<your-dsn>
   SENTRY_ENABLED=true
   ```

2. `RAILWAY_GIT_COMMIT_SHA` is automatically set ✅

### Docker

Add to `docker-compose.yml` or run command:

```yaml
environment:
  - SENTRY_DSN=${SENTRY_DSN}
  - SENTRY_ENABLED=true
  - RELEASE_SHA=${GITHUB_SHA}
```

Or:

```bash
docker run \
  -e SENTRY_DSN=<your-dsn> \
  -e SENTRY_ENABLED=true \
  -e RELEASE_SHA=$(git rev-parse HEAD) \
  gatewayz-api
```

---

## Configuration Checklist

- [ ] Sentry DSN configured
- [ ] `SENTRY_ENABLED=true` set
- [ ] Sample rates configured (start with 0.1)
- [ ] Release SHA automatically captured
- [ ] Test error appears in Sentry dashboard
- [ ] Release version shows correctly
- [ ] Environment tag correct (dev/staging/prod)

---

## What Gets Captured

✅ **Automatic:**
- All unhandled exceptions
- HTTP errors (4xx, 5xx)
- Provider API errors
- Database errors
- Request context (endpoint, method)
- User context (ID, email, credits, plan)
- Performance data (10% sampling by default)

❌ **Not Captured:**
- Health check endpoints (filtered)
- Expected validation errors (unless configured)
- API keys, passwords (automatically redacted)

---

## Common Issues

### "Sentry not initialized"

**Problem:** `SENTRY_ENABLED=false` or `SENTRY_DSN` not set

**Solution:** Check environment variables:
```bash
echo $SENTRY_ENABLED  # Should be "true"
echo $SENTRY_DSN      # Should be your DSN
```

### "Release shows as 'unknown'"

**Problem:** Release SHA not detected

**Solution:**
1. Check platform-specific env var is set:
   - Vercel: `VERCEL_GIT_COMMIT_SHA`
   - Railway: `RAILWAY_GIT_COMMIT_SHA`
   - CI/CD: `RELEASE_SHA` or `GITHUB_SHA`

2. Manually set:
   ```bash
   export RELEASE_SHA=$(git rev-parse HEAD)
   ```

### "Too many events (quota exceeded)"

**Problem:** High traffic + high sample rate

**Solution:** Lower sample rates:
```bash
SENTRY_TRACES_SAMPLE_RATE=0.01  # 1% instead of 10%
```

---

## Advanced Configuration

### Different Rates Per Environment

```bash
# Production (.env.production)
SENTRY_TRACES_SAMPLE_RATE=0.01
SENTRY_PROFILES_SAMPLE_RATE=0.01

# Staging (.env.staging)
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_PROFILES_SAMPLE_RATE=0.1

# Development (.env.development)
SENTRY_TRACES_SAMPLE_RATE=1.0
SENTRY_PROFILES_SAMPLE_RATE=0.5
```

### Custom Tags

Add custom tags in your code:

```python
from src.services.sentry_service import SentryService

SentryService.capture_exception(
    error,
    tags={"custom_category": "payment_error"}
)
```

### Filter Specific Errors

Edit `src/services/sentry_service.py`, in `_before_send`:

```python
@staticmethod
def _before_send(event, hint):
    # Drop health check errors
    if "/health" in event.get("request", {}).get("url", ""):
        return None

    # Drop expected validation errors
    if "ValidationError" in str(event.get("exception")):
        return None

    return event
```

---

## Monitoring Best Practices

### 1. Set Up Alerts

In Sentry dashboard:

1. **High-impact errors:**
   - Filter: `user_impact:high`
   - Alert when: `> 10 events in 5 minutes`
   - Notify: Slack/Email/PagerDuty

2. **New release errors:**
   - Filter: `release:latest`
   - Alert when: `> 5 new issues in 1 hour`

3. **Provider failures:**
   - Filter: `error_type:provider_error`
   - Alert when: `> 50 events in 10 minutes`

### 2. Review Weekly

- Check top errors by frequency
- Review new issues in recent releases
- Monitor error rate trends
- Update fingerprints if needed

### 3. Integrate with Workflow

- Link Sentry issues to GitHub/Jira
- Add Sentry context to PR reviews
- Include Sentry stats in sprint retrospectives

---

## Next Steps

1. ✅ **Setup complete** - Errors now tracked with full context
2. 📊 **Configure alerts** - Get notified of critical issues
3. 📈 **Monitor dashboard** - Weekly review of error trends
4. 🔧 **Fine-tune** - Adjust sample rates based on volume
5. 📚 **Read full docs** - See `docs/SENTRY_ERROR_LOGGING.md` for details

---

## Support

- **Sentry Docs:** [docs.sentry.io](https://docs.sentry.io)
- **Gatewayz Docs:** `docs/SENTRY_ERROR_LOGGING.md`
- **Report Issues:** Create GitHub issue with `[Sentry]` prefix

---

**Last Updated:** 2025-11-11
