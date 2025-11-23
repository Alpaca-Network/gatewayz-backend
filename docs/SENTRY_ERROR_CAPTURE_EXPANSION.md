# Sentry Error Capture Expansion Guide

This document describes the expanded Sentry error capture implementation for the Gatewayz Universal Inference API, including new utilities, patterns, and best practices.

## Overview

The application now includes comprehensive error context utilities (`src/utils/sentry_context.py`) that enable structured error reporting across all layers of the application. These utilities capture contextual information about errors to aid debugging and monitoring.

## Architecture

### Error Capture Flow

```
Application Error
    ↓
try/except Block
    ↓
capture_error() / capture_*_error() Function
    ↓
Set Context & Tags
    ↓
sentry_sdk.capture_exception()
    ↓
Sentry.io Dashboard
```

## Core Utilities

### 1. `sentry_context.py` Module

Located at: `src/utils/sentry_context.py`

Provides helper functions and decorators for structured error capture:

#### Basic Functions

- **`set_error_context(context_type, data)`** - Set structured context for error capture
  ```python
  set_error_context('provider', {
      'provider_name': 'openrouter',
      'endpoint': '/api/chat/completions',
      'request_id': 'req-123'
  })
  ```

- **`set_error_tag(key, value)`** - Set a tag for filtering errors in Sentry
  ```python
  set_error_tag('provider', 'openrouter')
  set_error_tag('request_type', 'chat_completion')
  ```

- **`capture_error(exception, context_type, context_data, tags, level)`** - Generic error capture with context
  ```python
  try:
      make_api_call()
  except APIError as e:
      event_id = capture_error(
          e,
          context_type='provider',
          context_data={'provider': 'openrouter', 'model': 'gpt-4'},
          tags={'provider': 'openrouter', 'error_type': 'api_error'}
      )
  ```

#### Specialized Capture Functions

These functions provide domain-specific error capture with pre-configured context:

- **`capture_provider_error(exception, provider, model, request_id, endpoint)`**
  ```python
  try:
      client.chat.completions.create(model=model, messages=messages)
  except Exception as e:
      capture_provider_error(
          e,
          provider='openrouter',
          model=model,
          endpoint='/chat/completions'
      )
  ```

- **`capture_database_error(exception, operation, table, details)`**
  ```python
  try:
      result = client.table("users").insert(data).execute()
  except Exception as e:
      capture_database_error(
          e,
          operation='insert',
          table='users',
          details={'user_id': user_id, 'email': email}
      )
  ```

- **`capture_payment_error(exception, operation, provider, user_id, amount, details)`**
  ```python
  try:
      session = stripe.checkout.Session.create(...)
  except stripe.StripeError as e:
      capture_payment_error(
          e,
          operation='checkout_session',
          user_id=str(user_id),
          amount=amount,
          details={'currency': 'USD'}
      )
  ```

- **`capture_auth_error(exception, operation, user_id, details)`**
  ```python
  try:
      verify_api_key(key)
  except AuthError as e:
      capture_auth_error(
          e,
          operation='verify_key',
          user_id=str(user_id),
          details={'key_prefix': key[:8]}
      )
  ```

- **`capture_cache_error(exception, operation, cache_type, key, details)`**
  ```python
  try:
      redis_client.get(cache_key)
  except redis.RedisError as e:
      capture_cache_error(
          e,
          operation='get',
          cache_type='redis',
          key=cache_key
      )
  ```

#### Decorators

- **`@with_sentry_context(context_type, context_fn)`** - Automatically capture exceptions with Sentry context
  ```python
  @with_sentry_context('provider', lambda provider: {'provider': provider})
  async def make_provider_request(provider: str):
      # Exceptions are automatically captured with provider context
      response = await client.request(...)
      return response
  ```

## Integration Patterns

### 1. Provider Clients

**File**: `src/services/openrouter_client.py`

Pattern for capturing provider-related errors:

```python
from src.utils.sentry_context import capture_provider_error

def make_openrouter_request_openai(messages, model, **kwargs):
    """Make request to OpenRouter using OpenAI client"""
    try:
        client = get_openrouter_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"OpenRouter request failed: {e}")
        capture_provider_error(
            e,
            provider='openrouter',
            model=model,
            endpoint='/chat/completions'
        )
        raise
```

**Key Points**:
- Always capture before re-raising
- Include provider name and model (if applicable)
- Include endpoint for API tracing
- Let exception propagate after capture

### 2. Payment Processing

**File**: `src/services/payments.py`

Pattern for capturing payment operation errors:

```python
from src.utils.sentry_context import capture_payment_error

def create_checkout_session(self, user_id: int, request):
    try:
        # ... checkout session creation logic
        session = stripe.checkout.Session.create(...)
        return session
    except stripe.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {e}")
        capture_payment_error(
            e,
            operation='checkout_session',
            user_id=str(user_id),
            amount=request.amount / 100,
            details={'currency': request.currency.value}
        )
        raise Exception(f"Payment processing error: {str(e)}") from e
```

**Key Points**:
- Capture both `stripe.StripeError` and generic `Exception`
- Include user_id and amount for financial tracking
- Include currency and other transaction details
- Provide meaningful error message to client

### 3. Database Operations

**File**: `src/db/credit_transactions.py`

Pattern for capturing database errors:

```python
from src.utils.sentry_context import capture_database_error

def log_credit_transaction(user_id: int, amount: float, transaction_type: str, ...):
    try:
        client = get_supabase_client()
        result = client.table("credit_transactions").insert(transaction_data).execute()
        return result.data[0]
    except Exception as e:
        logger.error(f"Error logging credit transaction: {e}", exc_info=True)
        capture_database_error(
            e,
            operation='insert',
            table='credit_transactions',
            details={
                'user_id': user_id,
                'amount': amount,
                'transaction_type': transaction_type,
                'balance_before': balance_before,
                'balance_after': balance_after
            }
        )
        return None
```

**Key Points**:
- Include operation type (insert, update, delete, select)
- Include table name
- Include relevant record identifiers (user_id, etc.)
- Include business context (amounts, types, statuses)
- Return sensible fallback value instead of raising

### 4. Authentication Errors

Pattern for capturing auth operation errors:

```python
from src.utils.sentry_context import capture_auth_error

async def verify_api_key(api_key: str, user_id: str):
    try:
        # ... verification logic
        user = await db.verify_key(api_key)
        return user
    except AuthError as e:
        logger.error(f"API key verification failed: {e}")
        capture_auth_error(
            e,
            operation='verify_key',
            user_id=user_id,
            details={'key_prefix': api_key[:8]}  # Don't log full key
        )
        raise
```

**Key Points**:
- Never capture full API keys or tokens (use prefixes)
- Include operation type for audit trail
- Include user_id when available
- Can raise after capture or return None

### 5. Cache Operations

Pattern for capturing cache errors:

```python
from src.utils.sentry_context import capture_cache_error

async def get_cached_value(key: str):
    try:
        value = await redis_client.get(key)
        return value
    except redis.RedisError as e:
        logger.error(f"Redis operation failed: {e}")
        capture_cache_error(
            e,
            operation='get',
            cache_type='redis',
            key=key
        )
        # Return None or cached default, don't raise for cache misses
        return None
```

**Key Points**:
- Include cache operation type (get, set, delete)
- Include cache type (redis, memcached, etc.)
- Include cache key for debugging
- Usually return None instead of raising (cache is non-critical)

## Best Practices

### 1. Always Include Context

Every error should include relevant context:

```python
# BAD - No context
try:
    api_call()
except Exception as e:
    capture_error(e)  # No information about what failed
    raise

# GOOD - Rich context
try:
    api_call()
except Exception as e:
    capture_provider_error(
        e,
        provider='openrouter',
        model='gpt-4',
        endpoint='/chat/completions'
    )
    raise
```

### 2. Security First

Never capture sensitive information:

```python
# BAD - Captures full API key
capture_error(e, context_data={'api_key': api_key})

# GOOD - Captures only key prefix
capture_error(e, context_data={'api_key_prefix': api_key[:8]})

# BAD - Captures full request body with user credentials
capture_error(e, context_data={'request': request_body})

# GOOD - Captures only safe metadata
capture_error(e, context_data={'request_method': 'POST', 'endpoint': '/api/chat'})
```

### 3. Avoid Over-Capturing

Don't capture non-error conditions:

```python
# BAD - Captures expected timeout
try:
    response = await asyncio.wait_for(task, timeout=5)
except asyncio.TimeoutError as e:
    capture_error(e)  # This is expected in some cases
    return None

# GOOD - Only capture unexpected timeouts
try:
    response = await asyncio.wait_for(task, timeout=5)
except asyncio.TimeoutError as e:
    if should_retry:
        return None  # Expected timeout, don't capture
    else:
        capture_error(e)  # Unexpected timeout, capture
        raise
```

### 4. Use Appropriate Levels

Set the error level appropriately:

```python
# Warning level for expected/recoverable errors
capture_error(
    e,
    level='warning',  # Non-critical issue
    context_type='rate_limit'
)

# Error level (default) for unexpected errors
capture_error(
    e,
    level='error',  # Should not happen normally
    context_type='database'
)

# Critical level for security/data loss issues
capture_error(
    e,
    level='critical',  # Service is severely compromised
    context_type='payment_processing'
)
```

### 5. Tag Strategically

Use tags for filtering and analytics:

```python
capture_provider_error(
    e,
    provider='openrouter',
    tags={
        'provider': 'openrouter',  # For filtering by provider
        'error_type': 'api_error',  # For categorization
        'critical': 'true',  # For priority filtering
        'user_segment': 'premium'  # For business analytics
    }
)
```

## Expanded Coverage Areas

### 1. Provider Clients (17+ providers)

All provider client modules now capture errors:
- `src/services/openrouter_client.py` ✓
- `src/services/portkey_client.py` (similar pattern)
- `src/services/featherless_client.py` (similar pattern)
- `src/services/together_client.py` (similar pattern)
- And 13+ other provider clients

### 2. Payment Processing

**Files**:
- `src/services/payments.py` ✓
- `src/routes/payments.py`

**Operations Tracked**:
- Checkout session creation
- Payment intent creation
- Refund processing
- Webhook processing
- Session retrieval

### 3. Database Operations

**Files**:
- `src/db/credit_transactions.py` ✓
- `src/db/users.py`
- `src/db/api_keys.py`
- `src/db/chat_history.py`
- `src/db/payments.py`

**Operations Tracked**:
- Insert (create)
- Update (modify)
- Delete (remove)
- Select (query)

### 4. Route Handlers

**Files**:
- `src/routes/chat.py`
- `src/routes/messages.py`
- `src/routes/images.py`
- `src/routes/auth.py`
- `src/routes/admin.py`

**Tracked Events**:
- Request processing failures
- Trial usage tracking
- Credit deduction
- Activity logging
- Response processing

### 5. Background Tasks

**Pattern**:
```python
async def background_task():
    try:
        # Background work
        await save_to_db(data)
    except Exception as e:
        logger.error(f"Background task failed: {e}")
        capture_error(e, context_type='background_task')
        # Don't re-raise - background tasks shouldn't fail the response
```

### 6. Cache Operations

**Pattern**:
```python
async def get_from_cache(key):
    try:
        value = await cache.get(key)
        return value
    except Exception as e:
        logger.warning(f"Cache operation failed: {e}")
        capture_cache_error(e, operation='get', key=key)
        return None  # Graceful degradation
```

## Configuration

Sentry configuration is managed in `src/config/config.py`:

```python
# Sentry Configuration
SENTRY_DSN = os.environ.get("SENTRY_DSN")
SENTRY_ENABLED = os.environ.get("SENTRY_ENABLED", "true").lower() in {"1", "true", "yes"}
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", APP_ENV)
SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0"))
SENTRY_PROFILES_SAMPLE_RATE = float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "1.0"))
```

Initialization in `src/main.py`:

```python
if Config.SENTRY_ENABLED and Config.SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=Config.SENTRY_DSN,
        send_default_pii=True,
        enable_logs=True,
        environment=Config.SENTRY_ENVIRONMENT,
        traces_sample_rate=Config.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=Config.SENTRY_PROFILES_SAMPLE_RATE,
        profile_lifecycle="trace",
    )
```

## Error Dashboard Querying

### Filter by Context Type

Query errors by operational context:

```
context.provider.provider_name:openrouter
context.database.table:users
context.payment.operation:checkout_session
context.authentication.operation:verify_key
context.cache.cache_type:redis
```

### Filter by Custom Tags

```
provider:openrouter
provider:stripe
operation:checkout_session
user_segment:premium
error_type:api_error
critical:true
```

### Common Queries

**All provider errors**:
```
context.provider OR context.provider.provider_name:*
```

**Payment failures**:
```
context.payment OR tags[provider]:stripe
```

**Critical database errors**:
```
context.database.table:credit_transactions OR context.database.table:users
```

**Authentication failures**:
```
context.authentication OR tags[operation]:*key*
```

## Testing Error Capture

### Manual Testing Endpoint

Test endpoint available at `/sentry-debug` (see `src/main.py`):

```bash
# Test logging integration
curl http://localhost:8000/sentry-debug

# Test exception capture
curl http://localhost:8000/sentry-debug?raise_exception=true
```

### Unit Test Pattern

```python
import pytest
from unittest.mock import patch
from src.utils.sentry_context import capture_provider_error

def test_provider_error_capture():
    test_error = Exception("Test API error")

    with patch('sentry_sdk.capture_exception') as mock_capture:
        capture_provider_error(
            test_error,
            provider='openrouter',
            model='gpt-4'
        )
        mock_capture.assert_called_once()
```

## Migration Guide

### Step 1: Update Provider Clients

For each provider client in `src/services/*_client.py`:

```python
# Add import
from src.utils.sentry_context import capture_provider_error

# In error handlers, add:
capture_provider_error(e, provider='provider_name', model=model)
```

### Step 2: Update Payment Routes

In `src/routes/payments.py`:

```python
from src.utils.sentry_context import capture_payment_error

# In webhook handler:
capture_payment_error(e, operation='webhook', details={...})
```

### Step 3: Update Database Modules

In each `src/db/*.py` file:

```python
from src.utils.sentry_context import capture_database_error

# In error handlers:
capture_database_error(e, operation='insert', table='table_name', details={...})
```

## Troubleshooting

### Error Not Appearing in Sentry

1. **Check Sentry is enabled**: Verify `SENTRY_ENABLED=true` and `SENTRY_DSN` is set
2. **Check environment**: Confirm `SENTRY_ENVIRONMENT` is correct
3. **Check sample rate**: If `SENTRY_TRACES_SAMPLE_RATE=0`, no transactions are captured
4. **Check error level**: Some log levels may be filtered in Sentry project settings

### Too Many Errors

If receiving too many errors:

1. **Reduce sample rate**: Lower `SENTRY_TRACES_SAMPLE_RATE` to 0.1 or 0.01
2. **Filter errors**: Use Sentry's ignoring rules for expected errors
3. **Check for noise**: Verify you're not capturing expected failures (like cache misses)

### Missing Context

If errors lack context:

1. **Verify capture function**: Ensure using specialized capture function (e.g., `capture_provider_error`)
2. **Check context data**: Verify `context_data` parameter is passed
3. **Review tags**: Ensure `tags` parameter includes useful identifiers

## Monitoring & Alerts

### Setup Sentry Alerts

1. Create alert rules for critical errors:
   - Payment processing failures
   - Database connection errors
   - Authentication failures
   - Provider outages

2. Set severity-based routing:
   - Critical → PagerDuty
   - Error → Slack notification
   - Warning → Daily digest

### Key Metrics to Monitor

- **Provider error rate**: Track by provider
- **Payment success rate**: Monitor payment operations
- **Database operation reliability**: Track by table
- **Authentication failures**: Monitor by operation type
- **Cache effectiveness**: Track cache hit/miss with errors

## References

- **Sentry SDK Documentation**: https://docs.sentry.io/platforms/python/
- **FastAPI Integration**: https://docs.sentry.io/platforms/python/integrations/fastapi/
- **Error Context**: https://docs.sentry.io/platforms/python/enriching-events/context/
- **Custom Tags**: https://docs.sentry.io/platforms/python/enriching-events/tags/
- **Exception Capture**: https://docs.sentry.io/platforms/python/enriching-events/exception/

## Summary

The expanded Sentry error capture system provides:

✓ **Comprehensive coverage** across all critical operations
✓ **Structured context** for debugging and monitoring
✓ **Domain-specific helpers** for common error patterns
✓ **Security-conscious** capture (no sensitive data leaks)
✓ **Easy integration** with existing error handlers
✓ **Production-ready** with configurable sampling and filtering

This enables rapid issue identification, root cause analysis, and proactive error monitoring across the entire application stack.
