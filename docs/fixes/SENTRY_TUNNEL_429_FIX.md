# Fix: Server 429 Error on Sentry Tunnel (POST /monitoring)

## Problem

The frontend application was experiencing 429 (Too Many Requests) errors when trying to send error tracking events to the backend's `/monitoring` endpoint:

```
POST https://beta.gatewayz.ai/monitoring?o=4510344966111232&p=4510344986099712&r=us 429 (Too Many Requests)
```

This was causing the frontend Sentry SDK to fail silently, preventing error tracking from working properly.

## Root Cause

The frontend Sentry SDK was configured with a **tunnel** pointing to `/monitoring` to bypass ad blockers. However:

1. Initially, the backend did not have a corresponding POST endpoint at `/monitoring`
2. After adding the endpoint, Sentry's ingestion servers may still return 429 errors when rate limited

The existing `/api/monitoring/*` endpoints (GET requests) were not related to this issue - they provide health metrics, analytics, and provider comparison data.

## Solution

### Changes Made

**File: `src/routes/monitoring.py`**

Added a new Sentry tunnel endpoint at `POST /monitoring` with:

1. Receives Sentry envelope data from the frontend SDK
2. Parses the envelope header to extract the DSN
3. Validates that the target is a legitimate Sentry host (security measure)
4. Forwards the envelope to Sentry's ingestion endpoint with **automatic retry on 429**
5. Returns Sentry's response to the frontend with appropriate `Retry-After` headers

**File: `src/main.py`**

Registered the new `sentry_tunnel_router` to expose the `/monitoring` POST endpoint at the application root level.

### Implementation Details

```python
# Retry configuration for Sentry tunnel
SENTRY_MAX_RETRIES = 3
SENTRY_BASE_DELAY = 0.5  # seconds
SENTRY_MAX_DELAY = 10.0  # seconds

# Allowed Sentry hosts for security
ALLOWED_SENTRY_HOSTS = {
    "sentry.io",
    "o4510344966111232.ingest.us.sentry.io",
    "ingest.sentry.io",
    "ingest.us.sentry.io",
}

async def _forward_to_sentry_with_retry(...) -> httpx.Response:
    # Exponential backoff retry on 429 errors
    # Respects Retry-After header from Sentry
    # Returns last response after retries exhausted

@sentry_tunnel_router.post("/monitoring")
async def sentry_tunnel(request: Request) -> Response:
    # 1. Read envelope body
    # 2. Parse JSON header to get DSN
    # 3. Validate Sentry host
    # 4. Forward to Sentry with retry logic
    # 5. Return response with Retry-After header on 429
```

### Rate Limit Handling (Updated 2025-12-05)

The endpoint now handles 429 responses from Sentry's ingestion servers:

1. **Automatic Retry**: Up to 3 retries with exponential backoff (0.5s, 1s, 2s)
2. **Retry-After Header**: Respects Sentry's `Retry-After` header when present
3. **Default Retry-After**: Returns `Retry-After: 60` if Sentry doesn't provide one
4. **Graceful Degradation**: After all retries, returns 429 with appropriate headers

### Security Considerations

1. **Host Validation**: Only forwards to known Sentry domains to prevent SSRF attacks
   - Uses **exact matching** or **proper subdomain checking** (requires dot prefix)
   - Blocks attacks like `evil-sentry.io` or `malicioussentry.io`
   - Rejects null/empty hostnames from malformed DSNs
2. **Input Validation**: Validates envelope format before processing
   - Rejects non-dict JSON payloads (arrays, strings, numbers, null)
   - Returns proper 400 errors for malformed requests
3. **No Authentication**: Intentionally public to allow frontend error tracking
4. **No Rate Limiting**: Should bypass any rate limiting to ensure errors are captured
5. **Timeout Handling**: 30-second timeout for Sentry requests
6. **Error Isolation**: Errors in the tunnel don't affect other application functionality

### Frontend Configuration

The frontend Sentry SDK should be configured with:

```javascript
Sentry.init({
  dsn: "https://<key>@<project>.ingest.us.sentry.io/<id>",
  tunnel: "/monitoring",
});
```

## Testing

### Unit Tests Added

```bash
# Run tests for the new endpoint
pytest tests/routes/test_monitoring.py::TestSentryTunnelEndpoint -v
```

Test cases:
- `test_sentry_tunnel_empty_body` - Returns 400 for empty requests
- `test_sentry_tunnel_invalid_envelope` - Returns 400 for malformed envelopes
- `test_sentry_tunnel_no_dsn` - Returns 400 when DSN is missing
- `test_sentry_tunnel_blocked_host` - Returns 403 for non-Sentry hosts
- `test_sentry_tunnel_ssrf_prevention_suffix_attack` - Blocks `evil-sentry.io` attacks
- `test_sentry_tunnel_ssrf_prevention_malicious_subdomain` - Blocks `malicioussentry.io` attacks
- `test_sentry_tunnel_null_hostname` - Rejects malformed DSN with null hostname
- `test_sentry_tunnel_non_dict_json` - Returns 400 for array JSON
- `test_sentry_tunnel_string_json` - Returns 400 for string JSON
- `test_sentry_tunnel_number_json` - Returns 400 for number JSON
- `test_sentry_tunnel_null_json` - Returns 400 for null JSON
- `test_sentry_tunnel_valid_envelope` - Forwards valid envelopes to Sentry
- `test_sentry_tunnel_429_retry_success` - Retries on 429 and succeeds
- `test_sentry_tunnel_429_all_retries_exhausted` - Returns 429 with Retry-After after all retries
- `test_sentry_tunnel_429_default_retry_after` - Returns default Retry-After when not provided
- `test_sentry_tunnel_http_error_retry` - Retries on HTTP connection errors

### Manual Testing

```bash
# Test the endpoint is accessible
curl -X POST https://api.gatewayz.ai/monitoring \
  -H "Content-Type: application/x-sentry-envelope" \
  -d '{"dsn":"https://key@sentry.io/123"}'
```

## Deployment

No breaking changes. The fix adds a new endpoint that doesn't affect existing functionality.

### Verification Steps

1. Deploy the updated backend
2. Open the frontend application
3. Trigger an error (e.g., in dev console)
4. Verify no 429 errors in browser console
5. Check Sentry dashboard for received events

## Related Files

- `src/routes/monitoring.py` - Sentry tunnel endpoint
- `src/main.py` - Router registration
- `tests/routes/test_monitoring.py` - Unit tests
- `docs/fixes/MONITORING_429_FIX.md` - Previous fix for `/api/monitoring/*` endpoints

## Date

2025-12-02

## Commit Message

```
fix(monitoring): add Sentry tunnel endpoint to fix frontend 429 errors

Add POST /monitoring endpoint to act as a Sentry tunnel for the frontend SDK.
This allows frontend error tracking to work even when ad blockers block
direct requests to sentry.io.

Changes:
- Add sentry_tunnel_router with POST /monitoring endpoint
- Parse Sentry envelope format and forward to Sentry servers
- Validate target hosts to prevent SSRF attacks
- Add comprehensive unit tests for the new endpoint

The endpoint is intentionally public (no auth) to allow frontend error
tracking without exposing API keys. Host validation ensures only
legitimate Sentry domains receive forwarded events.

Fixes: Server 429 error on POST /monitoring from frontend Sentry SDK
```
