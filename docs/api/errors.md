# API Error Handling Guide

Comprehensive guide to error handling in the Gatewayz API.

## Overview

The Gatewayz API uses detailed, structured error responses to help you quickly identify and resolve issues. Every error includes:

- **Clear error messages** explaining what went wrong
- **Actionable suggestions** for how to fix the problem
- **Relevant context** to help with debugging
- **Documentation links** for additional information
- **Request IDs** for tracking and support

## Error Response Format

All errors follow a standardized JSON structure:

```json
{
  "error": {
    "type": "model_not_found",
    "message": "Model 'gpt-5-ultra' not found",
    "detail": "The requested model is not available in our catalog. Did you mean 'gpt-4'?",
    "code": "MODEL_NOT_FOUND",
    "status": 404,
    "request_id": "req_abc123",
    "timestamp": "2025-01-21T12:00:00Z",
    "suggestions": [
      "Check available models at /v1/models",
      "Try using 'gpt-4' or 'gpt-3.5-turbo' instead",
      "Visit https://docs.gatewayz.ai/models for model list"
    ],
    "context": {
      "requested_model": "gpt-5-ultra",
      "suggested_models": ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
      "provider": "openrouter"
    },
    "docs_url": "https://docs.gatewayz.ai/errors/model-not-found"
  }
}
```

### Error Fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Snake_case error type identifier |
| `message` | string | Short, human-readable error description |
| `detail` | string | Detailed explanation of the error |
| `code` | string | UPPER_SNAKE_CASE error code constant |
| `status` | integer | HTTP status code (400-599) |
| `request_id` | string | Unique request identifier for support |
| `timestamp` | string | ISO 8601 timestamp when error occurred |
| `suggestions` | array | List of actionable next steps |
| `context` | object | Additional debugging information |
| `docs_url` | string | Link to relevant documentation |
| `support_url` | string | Link to support (for payment/billing errors) |

## Error Categories

### 1. Model Errors (404, 410, 503)

Errors related to model availability and configuration.

#### MODEL_NOT_FOUND (404)

The requested model doesn't exist in the catalog.

**Example:**
```json
{
  "error": {
    "type": "model_not_found",
    "code": "MODEL_NOT_FOUND",
    "status": 404,
    "message": "Model 'gpt-5' not found",
    "context": {
      "requested_model": "gpt-5",
      "suggested_models": ["gpt-4", "gpt-4-turbo"]
    }
  }
}
```

**Common causes:**
- Typo in model name
- Model doesn't exist
- Using deprecated model ID

**How to fix:**
- Check /v1/models for available models
- Verify spelling of model name
- Use suggested alternatives

#### MODEL_UNAVAILABLE (503)

Model exists but is temporarily unavailable.

**Common causes:**
- Provider maintenance
- Provider outage
- Temporary capacity issues

**How to fix:**
- Try again in a few minutes
- Use an alternative model
- Check https://status.gatewayz.ai

#### MODEL_DEPRECATED (410)

Model has been deprecated and is no longer available.

**How to fix:**
- Check model catalog for alternatives
- Update to newer model version
- See migration guide at docs

### 2. Validation Errors (400, 422)

Errors related to invalid request parameters or format.

#### MISSING_REQUIRED_FIELD (400)

A required parameter is missing from the request.

**Example:**
```json
{
  "error": {
    "type": "missing_required_field",
    "code": "MISSING_REQUIRED_FIELD",
    "status": 400,
    "message": "Missing required field: 'messages'",
    "context": {
      "field_name": "messages"
    }
  }
}
```

#### PARAMETER_OUT_OF_RANGE (400)

Parameter value exceeds valid range.

**Example:**
```json
{
  "error": {
    "type": "parameter_out_of_range",
    "code": "PARAMETER_OUT_OF_RANGE",
    "status": 400,
    "message": "Parameter 'temperature' value 5.0 is out of valid range [0.0, 2.0]",
    "context": {
      "parameter_name": "temperature",
      "parameter_value": 5.0,
      "min_value": 0.0,
      "max_value": 2.0
    }
  }
}
```

#### CONTEXT_LENGTH_EXCEEDED (400)

Input is too long for the model's context window.

**Example:**
```json
{
  "error": {
    "type": "context_length_exceeded",
    "code": "CONTEXT_LENGTH_EXCEEDED",
    "status": 400,
    "message": "Input length (150000 tokens) exceeds model's maximum context length (128000)",
    "context": {
      "input_tokens": 150000,
      "max_context_length": 128000
    },
    "suggestions": [
      "Reduce input length",
      "Use a model with larger context (e.g., GPT-4 Turbo 128k)",
      "Split request into smaller chunks"
    ]
  }
}
```

### 3. Authentication Errors (401)

Errors related to API key authentication.

#### INVALID_API_KEY (401)

API key is invalid, not found, or malformed.

**Example:**
```json
{
  "error": {
    "type": "invalid_api_key",
    "code": "INVALID_API_KEY",
    "status": 401,
    "message": "Invalid API key",
    "suggestions": [
      "Verify your API key at https://gatewayz.ai/dashboard",
      "Ensure you're using the correct environment (test vs live)",
      "Generate a new API key if needed"
    ]
  }
}
```

**Common causes:**
- Typo in API key
- Using test key in production
- Key has been deleted
- Key not included in request

**How to fix:**
- Copy API key from dashboard
- Include as `Authorization: Bearer YOUR_API_KEY`
- Verify no extra spaces or characters

#### API_KEY_EXPIRED (401)

API key has reached its expiration date.

**How to fix:**
- Generate new API key from dashboard
- Update application configuration

#### API_KEY_REVOKED (401)

API key has been manually revoked.

**How to fix:**
- Create new API key
- Update application with new key

### 4. Authorization Errors (403)

Errors related to permissions and access control.

#### IP_RESTRICTED (403)

Request from unauthorized IP address.

**Example:**
```json
{
  "error": {
    "type": "ip_restricted",
    "code": "IP_RESTRICTED",
    "status": 403,
    "message": "Access denied: IP address 1.2.3.4 is not in the allowed list",
    "context": {
      "ip_address": "1.2.3.4"
    },
    "suggestions": [
      "Add your IP to the allowed list in dashboard",
      "Disable IP restrictions for dynamic IPs"
    ]
  }
}
```

#### TRIAL_EXPIRED (403)

Free trial period has ended.

**How to fix:**
- Upgrade to paid plan
- Add credits to account

#### PLAN_LIMIT_REACHED (403)

Subscription plan limit has been exceeded.

**How to fix:**
- Upgrade plan for higher limits
- Wait for quota reset
- Contact support for custom limits

### 5. Payment & Credit Errors (402)

Errors related to billing and credits.

#### INSUFFICIENT_CREDITS (402)

Not enough credits to complete the request.

**Example:**
```json
{
  "error": {
    "type": "insufficient_credits",
    "code": "INSUFFICIENT_CREDITS",
    "status": 402,
    "message": "Insufficient credits. Required: $2.00, Current: $0.50",
    "detail": "You need $1.50 more credits to complete this request.",
    "context": {
      "current_credits": 0.50,
      "required_credits": 2.00,
      "credit_deficit": 1.50
    },
    "suggestions": [
      "Add credits at https://gatewayz.ai/billing",
      "Enable auto-recharge to prevent interruptions",
      "Consider upgrading to subscription for better rates"
    ],
    "support_url": "https://gatewayz.ai/support"
  }
}
```

**How to fix:**
- Add credits to account
- Enable auto-recharge
- Upgrade to subscription plan

### 6. Rate Limiting Errors (429)

Errors when rate limits are exceeded.

#### RATE_LIMIT_EXCEEDED (429)

Too many requests in the time window.

**Example:**
```json
{
  "error": {
    "type": "rate_limit_exceeded",
    "code": "RATE_LIMIT_EXCEEDED",
    "status": 429,
    "message": "Rate limit exceeded: requests_per_minute",
    "context": {
      "limit_type": "requests_per_minute",
      "limit_value": 60,
      "current_usage": 61,
      "retry_after": 45,
      "reset_time": "2025-01-21T12:01:00Z"
    },
    "suggestions": [
      "Wait 45 seconds before retrying",
      "Implement exponential backoff",
      "Upgrade plan for higher limits"
    ]
  }
}
```

**Headers included:**
- `Retry-After`: Seconds until retry is allowed
- `X-RateLimit-Limit`: Rate limit threshold
- `X-RateLimit-Remaining`: Remaining requests
- `X-RateLimit-Reset`: When limit resets (Unix timestamp)

**How to fix:**
- Check `Retry-After` header
- Implement exponential backoff
- Upgrade plan for higher limits

#### DAILY_QUOTA_EXCEEDED (429)

Daily usage quota has been exceeded.

**How to fix:**
- Wait for midnight UTC reset
- Upgrade plan for higher quota

#### TOKEN_RATE_LIMIT (429)

Token throughput limit exceeded.

**How to fix:**
- Reduce tokens per request
- Spread requests over time
- Upgrade plan

### 7. Provider Errors (502, 503, 504)

Errors from upstream AI providers.

#### PROVIDER_ERROR (502)

Provider returned an error.

**Example:**
```json
{
  "error": {
    "type": "provider_error",
    "code": "PROVIDER_ERROR",
    "status": 502,
    "message": "Provider 'openrouter' returned an error for model 'gpt-4': Rate limit exceeded",
    "context": {
      "provider": "openrouter",
      "requested_model": "gpt-4",
      "provider_error_message": "Rate limit exceeded",
      "provider_status_code": 429
    },
    "suggestions": [
      "Try again in a few moments",
      "Try a different model from another provider",
      "Gatewayz handles automatic failover"
    ]
  }
}
```

**Common causes:**
- Provider rate limits
- Provider outages
- Provider API errors

**How to fix:**
- Retry the request
- Use alternative provider/model
- Gatewayz auto-retries with failover

#### PROVIDER_TIMEOUT (504)

Request to provider timed out.

**How to fix:**
- Retry request
- Use different provider
- Check provider status

#### ALL_PROVIDERS_FAILED (502)

All failover providers failed.

**How to fix:**
- Wait and retry
- Check https://status.gatewayz.ai
- Contact support if persistent

### 8. Server Errors (500, 503)

Internal service errors.

#### INTERNAL_ERROR (500)

Unexpected internal server error.

**Example:**
```json
{
  "error": {
    "type": "internal_error",
    "code": "INTERNAL_ERROR",
    "status": 500,
    "message": "Internal server error",
    "request_id": "req_xyz789",
    "suggestions": [
      "Try your request again",
      "Contact support with request_id if error persists"
    ]
  }
}
```

**How to fix:**
- Retry request
- Contact support with request_id

#### SERVICE_UNAVAILABLE (503)

Service temporarily unavailable.

**How to fix:**
- Wait and retry
- Check https://status.gatewayz.ai

## Using Request IDs for Support

Every error includes a unique `request_id`. When contacting support:

1. Include the full request_id
2. Provide the timestamp
3. Describe what you were trying to do
4. Include the model and endpoint used

Example support request:
```
Request ID: req_abc123
Timestamp: 2025-01-21T12:00:00Z
Endpoint: POST /v1/chat/completions
Model: gpt-4
Error: Received 502 error when requesting chat completion
```

## Best Practices

### 1. Handle Errors Gracefully

```python
import requests

try:
    response = requests.post(
        "https://api.gatewayz.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
    )
    response.raise_for_status()
    result = response.json()
except requests.exceptions.HTTPError as e:
    error = e.response.json()["error"]

    # Log request_id for support
    print(f"Error ({error['code']}): {error['message']}")
    print(f"Request ID: {error['request_id']}")

    # Show suggestions to user
    if "suggestions" in error:
        print("Suggestions:")
        for suggestion in error["suggestions"]:
            print(f"  - {suggestion}")
```

### 2. Implement Retry Logic

```python
import time
from requests.exceptions import HTTPError

def make_request_with_retry(url, headers, data, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except HTTPError as e:
            error = e.response.json()["error"]

            # Check if error is retryable
            if error["status"] in [429, 503, 504]:
                # Get retry_after from header or context
                retry_after = int(e.response.headers.get("Retry-After", 5))
                if attempt < max_retries - 1:
                    print(f"Rate limited. Retrying in {retry_after}s...")
                    time.sleep(retry_after)
                    continue

            # Non-retryable error
            raise

    raise Exception("Max retries exceeded")
```

### 3. Check Error Types

```python
error = response.json()["error"]

if error["code"] == "MODEL_NOT_FOUND":
    # Suggest alternative models from context
    if "suggested_models" in error.get("context", {}):
        alternatives = error["context"]["suggested_models"]
        print(f"Try one of these models instead: {', '.join(alternatives)}")

elif error["code"] == "INSUFFICIENT_CREDITS":
    # Show credit info
    context = error["context"]
    print(f"Need ${context['credit_deficit']:.2f} more credits")
    print(f"Add credits at: {error['support_url']}")

elif error["code"] == "RATE_LIMIT_EXCEEDED":
    # Implement backoff
    retry_after = error["context"].get("retry_after", 60)
    time.sleep(retry_after)
```

### 4. Log Errors Properly

```python
import logging

logger = logging.getLogger(__name__)

try:
    response = make_api_request()
except HTTPError as e:
    error = e.response.json()["error"]

    # Log with structured data
    logger.error(
        f"API error: {error['message']}",
        extra={
            "error_code": error["code"],
            "error_type": error["type"],
            "request_id": error["request_id"],
            "status": error["status"],
            "context": error.get("context", {})
        }
    )
```

## Error Code Quick Reference

| Error Code | Status | Category | Retryable |
|------------|--------|----------|-----------|
| MODEL_NOT_FOUND | 404 | Model | No |
| MODEL_UNAVAILABLE | 503 | Model | Yes |
| INVALID_API_KEY | 401 | Auth | No |
| INSUFFICIENT_CREDITS | 402 | Payment | No |
| RATE_LIMIT_EXCEEDED | 429 | Rate Limit | Yes |
| PROVIDER_ERROR | 502 | Provider | Yes |
| PROVIDER_TIMEOUT | 504 | Provider | Yes |
| INTERNAL_ERROR | 500 | Server | Yes |
| SERVICE_UNAVAILABLE | 503 | Server | Yes |

## Additional Resources

- [API Reference](https://docs.gatewayz.ai/api)
- [Model Catalog](https://docs.gatewayz.ai/models)
- [Authentication Guide](https://docs.gatewayz.ai/authentication)
- [Rate Limits](https://docs.gatewayz.ai/rate-limits)
- [Status Page](https://status.gatewayz.ai)
- [Support](https://gatewayz.ai/support)

## Changelog

**2025-01-21**
- Initial comprehensive error handling system
- Added detailed error responses
- Added context and suggestions
- Added request_id tracking
