# Sentry Error Capture - Quick Reference

Fast reference for adding Sentry error capture to the Gatewayz codebase.

## Import

```python
from src.utils.sentry_context import (
    capture_error,
    capture_provider_error,
    capture_database_error,
    capture_payment_error,
    capture_auth_error,
    capture_cache_error,
    set_error_context,
    set_error_tag,
)
```

## Quick Patterns

### Provider API Errors

```python
try:
    response = client.chat.completions.create(...)
except Exception as e:
    logger.error(f"Provider request failed: {e}")
    capture_provider_error(e, provider='openrouter', model=model, endpoint='/chat/completions')
    raise
```

### Database Errors

```python
try:
    result = client.table("users").insert(data).execute()
except Exception as e:
    logger.error(f"Database error: {e}")
    capture_database_error(e, operation='insert', table='users', details={'user_id': user_id})
    raise
```

### Payment/Stripe Errors

```python
try:
    session = stripe.checkout.Session.create(...)
except stripe.StripeError as e:
    logger.error(f"Stripe error: {e}")
    capture_payment_error(e, operation='checkout_session', user_id=str(user_id), amount=amount)
    raise
```

### Authentication Errors

```python
try:
    user = verify_api_key(api_key)
except AuthError as e:
    logger.error(f"Auth error: {e}")
    capture_auth_error(e, operation='verify_key', user_id=user_id)
    raise
```

### Cache Errors

```python
try:
    value = redis.get(key)
except redis.RedisError as e:
    logger.warning(f"Cache error: {e}")
    capture_cache_error(e, operation='get', cache_type='redis', key=key)
    return None  # Don't raise for cache failures
```

### Generic Errors

```python
try:
    some_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    capture_error(
        e,
        context_type='custom_context',
        context_data={'param1': value1, 'param2': value2},
        tags={'tag1': 'value1', 'operation': 'custom_op'}
    )
    raise
```

## Function Signatures

### `capture_provider_error()`
```python
capture_provider_error(
    exception: Exception,
    provider: str,                    # 'openrouter', 'portkey', etc.
    model: str | None = None,
    request_id: str | None = None,
    endpoint: str | None = None,
)
```

### `capture_database_error()`
```python
capture_database_error(
    exception: Exception,
    operation: str,                   # 'insert', 'update', 'delete', 'select'
    table: str,                       # table name
    details: dict[str, Any] | None = None,
)
```

### `capture_payment_error()`
```python
capture_payment_error(
    exception: Exception,
    operation: str,                   # 'checkout_session', 'payment_intent', 'refund', 'webhook'
    provider: str = 'stripe',
    user_id: str | None = None,
    amount: float | None = None,
    details: dict[str, Any] | None = None,
)
```

### `capture_auth_error()`
```python
capture_auth_error(
    exception: Exception,
    operation: str,                   # 'login', 'verify_key', 'validate_token'
    user_id: str | None = None,
    details: dict[str, Any] | None = None,
)
```

### `capture_cache_error()`
```python
capture_cache_error(
    exception: Exception,
    operation: str,                   # 'get', 'set', 'delete'
    cache_type: str = 'redis',
    key: str | None = None,
    details: dict[str, Any] | None = None,
)
```

### `capture_error()`
```python
capture_error(
    exception: Exception,
    context_type: str | None = None,
    context_data: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
    level: str = "error",
)
```

## Common Context Types

| Type | Usage | Example |
|------|-------|---------|
| `provider` | AI model provider errors | OpenRouter, Portkey, etc. |
| `database` | Database operation errors | INSERT, UPDATE, SELECT |
| `payment` | Payment processing errors | Stripe, checkout, refunds |
| `authentication` | Auth operation errors | Login, token validation |
| `cache` | Cache operation errors | Redis, memcached |
| `background_task` | Async task failures | Background jobs |
| `image_generation` | Image provider errors | Fal.ai, Google Vertex |

## Tagging Strategy

**Recommended tags for filtering**:

```python
tags = {
    'provider': 'openrouter',           # Which provider
    'operation': 'chat_completion',    # What operation
    'user_segment': 'premium',         # Business segment
    'error_type': 'api_error',         # Error classification
    'critical': 'true',                # Priority level
    'recoverable': 'false',            # Can auto-retry?
}
```

## Error Levels

- **`'info'`**: Informational, no action needed
- **`'warning'`**: Non-critical issue, expected in some cases
- **`'error'`** (default): Unexpected error, should investigate
- **`'fatal'`**: Application-breaking error, immediate action needed

## Testing

### Check Sentry Configuration

```python
import sentry_sdk

# Check if initialized
if sentry_sdk.Hub.current.client:
    print("Sentry is enabled")
else:
    print("Sentry is disabled")
```

### Test Error Capture

```bash
# Trigger test error capture
curl http://localhost:8000/sentry-debug

# View logs
docker logs -f <container>
```

### Manual Unit Test

```python
from unittest.mock import patch
from src.utils.sentry_context import capture_provider_error

def test_error_capture():
    error = Exception("Test error")
    with patch('sentry_sdk.capture_exception') as mock:
        capture_provider_error(error, provider='test', model='gpt-4')
        assert mock.called
```

## Do's and Don'ts

### ✓ DO

- Capture all unexpected errors
- Include relevant context data
- Use specialized functions (e.g., `capture_provider_error`)
- Tag errors for easy filtering
- Capture before re-raising exception

### ✗ DON'T

- Capture expected errors (cache misses, timeouts)
- Include sensitive data (API keys, tokens, passwords)
- Capture the same error multiple times
- Use generic `Exception` type when more specific available
- Forget to set context before capturing

## Key Files

| File | Purpose |
|------|---------|
| `src/utils/sentry_context.py` | Capture functions and utilities |
| `src/config/config.py` | Sentry configuration variables |
| `src/main.py` | Sentry initialization |
| `src/services/payments.py` | Payment error capture examples |
| `src/services/openrouter_client.py` | Provider error capture examples |
| `src/db/credit_transactions.py` | Database error capture examples |

## Environment Variables

```bash
# Enable/disable Sentry
SENTRY_ENABLED=true

# Sentry project DSN
SENTRY_DSN=https://your-key@sentry.io/project-id

# Environment tag (development, staging, production)
SENTRY_ENVIRONMENT=production

# Transaction sampling (0.0 - 1.0)
SENTRY_TRACES_SAMPLE_RATE=0.1

# Profile sampling (0.0 - 1.0)
SENTRY_PROFILES_SAMPLE_RATE=0.1
```

## Common Issues

### Error Not Showing in Sentry

1. Verify `SENTRY_ENABLED=true`
2. Check `SENTRY_DSN` is valid
3. Ensure `SENTRY_ENVIRONMENT` is set
4. Check sampling rates aren't too low

### Missing Context

1. Use domain-specific function (e.g., `capture_provider_error`)
2. Verify `context_data` is passed
3. Check `tags` are being set

### Too Many Errors

1. Reduce `SENTRY_TRACES_SAMPLE_RATE`
2. Filter expected errors using Sentry's ignore rules
3. Review if errors are truly unexpected

## Examples

See full examples in:
- `docs/SENTRY_ERROR_CAPTURE_EXPANSION.md` - Complete guide
- `src/services/payments.py` - Payment error handling
- `src/services/openrouter_client.py` - Provider error handling
- `src/db/credit_transactions.py` - Database error handling

## Support

For detailed documentation, see: `docs/SENTRY_ERROR_CAPTURE_EXPANSION.md`

For Sentry SDK docs: https://docs.sentry.io/platforms/python/
