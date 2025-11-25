# Model Health Tracking API Specification

**Version**: 1.0
**Base URL**: `/v1`
**Authentication**: Bearer token (API key)

---

## Overview

The Model Health Tracking API provides real-time and historical health metrics for all AI models across multiple providers. Track response times, success rates, error patterns, and availability.

**Features**:
- Real-time health monitoring
- Provider-specific metrics
- Unhealthy model detection
- Aggregate statistics
- Automatic tracking (no manual instrumentation required)

---

## Endpoints

### 1. List All Model Health Records

**GET** `/v1/model-health`

Retrieve health metrics for all monitored models with optional filtering and pagination.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `provider` | string | No | - | Filter by provider name (e.g., "openrouter") |
| `status` | string | No | - | Filter by last status (e.g., "success", "error") |
| `limit` | integer | No | 100 | Max records to return (1-1000) |
| `offset` | integer | No | 0 | Number of records to skip |

#### Response Schema

```json
{
  "total": 127,
  "limit": 100,
  "offset": 0,
  "filters": {
    "provider": "openrouter",
    "status": "success"
  },
  "models": [
    {
      "provider": "string",
      "model": "string",
      "last_response_time_ms": 0,
      "last_status": "string",
      "last_called_at": "string (ISO 8601)",
      "call_count": 0,
      "success_count": 0,
      "error_count": 0,
      "average_response_time_ms": 0,
      "last_error_message": "string | null",
      "created_at": "string (ISO 8601)",
      "updated_at": "string (ISO 8601)"
    }
  ]
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Invalid query parameters |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health?provider=openrouter&limit=50" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "total": 45,
  "limit": 50,
  "offset": 0,
  "filters": {
    "provider": "openrouter",
    "status": null
  },
  "models": [
    {
      "provider": "openrouter",
      "model": "anthropic/claude-3-opus",
      "last_response_time_ms": 1250.5,
      "last_status": "success",
      "last_called_at": "2025-11-24T12:30:45.123Z",
      "call_count": 1523,
      "success_count": 1498,
      "error_count": 25,
      "average_response_time_ms": 1180.2,
      "last_error_message": null,
      "created_at": "2025-11-20T08:15:00.000Z",
      "updated_at": "2025-11-24T12:30:45.123Z"
    }
  ]
}
```

---

### 2. Get Specific Model Health

**GET** `/v1/model-health/{provider}/{model}`

Get detailed health metrics for a specific provider-model combination.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider name (e.g., "openrouter") |
| `model` | string | Yes | Model identifier (e.g., "anthropic/claude-3-opus") |

#### Response Schema

```json
{
  "provider": "string",
  "model": "string",
  "last_response_time_ms": 0,
  "last_status": "string",
  "last_called_at": "string (ISO 8601)",
  "call_count": 0,
  "success_count": 0,
  "error_count": 0,
  "average_response_time_ms": 0,
  "last_error_message": "string | null",
  "created_at": "string (ISO 8601)",
  "updated_at": "string (ISO 8601)"
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 404 | Model health data not found |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health/openrouter/anthropic%2Fclaude-3-opus" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "provider": "openrouter",
  "model": "anthropic/claude-3-opus",
  "last_response_time_ms": 1250.5,
  "last_status": "success",
  "last_called_at": "2025-11-24T12:30:45.123Z",
  "call_count": 1523,
  "success_count": 1498,
  "error_count": 25,
  "average_response_time_ms": 1180.2,
  "last_error_message": null,
  "created_at": "2025-11-20T08:15:00.000Z",
  "updated_at": "2025-11-24T12:30:45.123Z"
}
```

---

### 3. Get Unhealthy Models

**GET** `/v1/model-health/unhealthy`

Retrieve models with high error rates (unhealthy models).

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `error_threshold` | float | No | 0.2 | Min error rate to be unhealthy (0.0-1.0) |
| `min_calls` | integer | No | 10 | Min calls required to evaluate health |

#### Response Schema

```json
{
  "threshold": 0.2,
  "min_calls": 10,
  "total_unhealthy": 0,
  "models": [
    {
      "provider": "string",
      "model": "string",
      "last_response_time_ms": 0,
      "last_status": "string",
      "call_count": 0,
      "success_count": 0,
      "error_count": 0,
      "error_rate": 0,
      "average_response_time_ms": 0,
      "last_error_message": "string | null"
    }
  ]
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Invalid query parameters |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health/unhealthy?error_threshold=0.15&min_calls=20" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "threshold": 0.15,
  "min_calls": 20,
  "total_unhealthy": 2,
  "models": [
    {
      "provider": "huggingface",
      "model": "meta-llama/Llama-3-70b",
      "last_response_time_ms": 2500.0,
      "last_status": "timeout",
      "call_count": 50,
      "success_count": 30,
      "error_count": 20,
      "error_rate": 0.4,
      "average_response_time_ms": 2100.5,
      "last_error_message": "Request timeout after 30s"
    },
    {
      "provider": "featherless",
      "model": "mixtral-8x7b",
      "last_response_time_ms": 0,
      "last_status": "error",
      "call_count": 25,
      "success_count": 20,
      "error_count": 5,
      "error_rate": 0.2,
      "average_response_time_ms": 1800.0,
      "last_error_message": "Connection refused"
    }
  ]
}
```

---

### 4. Get Overall Statistics

**GET** `/v1/model-health/stats`

Get aggregate statistics across all monitored models.

#### Response Schema

```json
{
  "total_models": 0,
  "total_calls": 0,
  "total_success": 0,
  "total_errors": 0,
  "average_response_time": 0,
  "success_rate": 0
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health/stats" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "total_models": 127,
  "total_calls": 45623,
  "total_success": 44891,
  "total_errors": 732,
  "average_response_time": 1345.7,
  "success_rate": 0.9839
}
```

---

### 5. Get Provider Summary

**GET** `/v1/model-health/provider/{provider}/summary`

Get health summary for all models from a specific provider.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider` | string | Yes | Provider name (e.g., "openrouter") |

#### Response Schema

```json
{
  "provider": "string",
  "total_models": 0,
  "total_calls": 0,
  "total_success": 0,
  "total_errors": 0,
  "average_response_time": 0,
  "success_rate": 0
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 404 | Provider not found |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health/provider/openrouter/summary" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "provider": "openrouter",
  "total_models": 45,
  "total_calls": 23456,
  "total_success": 23120,
  "total_errors": 336,
  "average_response_time": 1250.3,
  "success_rate": 0.9856
}
```

---

### 6. Get All Providers

**GET** `/v1/model-health/providers`

Get list of all providers with basic statistics.

#### Response Schema

```json
{
  "total_providers": 0,
  "providers": [
    {
      "provider": "string",
      "model_count": 0,
      "total_calls": 0
    }
  ]
}
```

#### Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 500 | Internal server error |

#### Example Request

```bash
curl -X GET "https://api.gatewayz.ai/v1/model-health/providers" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Example Response

```json
{
  "total_providers": 8,
  "providers": [
    {
      "provider": "openrouter",
      "model_count": 45,
      "total_calls": 23456
    },
    {
      "provider": "huggingface",
      "model_count": 52,
      "total_calls": 15234
    },
    {
      "provider": "together",
      "model_count": 38,
      "total_calls": 12789
    }
  ]
}
```

---

## Data Models

### ModelHealth

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Provider name |
| `model` | string | Model identifier |
| `last_response_time_ms` | number | Last response time in milliseconds |
| `last_status` | string | Last call status |
| `last_called_at` | string | ISO 8601 timestamp |
| `call_count` | integer | Total number of calls |
| `success_count` | integer | Number of successful calls |
| `error_count` | integer | Number of failed calls |
| `average_response_time_ms` | number | Average response time |
| `last_error_message` | string\|null | Last error message |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |

### Status Values

| Value | Description |
|-------|-------------|
| `success` | Call completed successfully |
| `error` | General error occurred |
| `timeout` | Request timed out |
| `rate_limited` | Rate limit exceeded (429) |
| `network_error` | Network/connection error |

---

## Rate Limiting

All model health endpoints are subject to rate limiting:

- **Rate Limit**: 1000 requests per hour per API key
- **Headers**:
  - `X-RateLimit-Limit`: Total allowed requests
  - `X-RateLimit-Remaining`: Remaining requests
  - `X-RateLimit-Reset`: Unix timestamp when limit resets

---

## Error Responses

### Error Schema

```json
{
  "detail": "string"
}
```

### Common Error Codes

| Code | Description | Example |
|------|-------------|---------|
| 400 | Bad Request | Invalid query parameters |
| 401 | Unauthorized | Missing or invalid API key |
| 404 | Not Found | Model health data not found |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server-side error |

### Example Error Response

```json
{
  "detail": "No health data found for provider 'invalid-provider' and model 'test-model'"
}
```

---

## Best Practices

### 1. Pagination

For large datasets, use pagination:

```bash
# First page
GET /v1/model-health?limit=50&offset=0

# Second page
GET /v1/model-health?limit=50&offset=50
```

### 2. Filtering

Combine filters to narrow results:

```bash
GET /v1/model-health?provider=openrouter&status=success&limit=25
```

### 3. Caching

Health data updates in real-time but changes gradually:
- Cache responses for 30-60 seconds
- Use ETags for conditional requests (future enhancement)

### 4. Polling

For dashboards, poll at reasonable intervals:
- Overall stats: Every 60 seconds
- Model list: Every 30-60 seconds
- Unhealthy models: Every 5 minutes

### 5. Error Handling

Always handle potential errors gracefully:

```typescript
try {
  const response = await fetch('/v1/model-health/stats');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  // Use data
} catch (error) {
  console.error('Failed to fetch health stats:', error);
  // Show fallback UI
}
```

---

## Use Cases

### Use Case 1: Dashboard KPIs

```typescript
// Fetch overall statistics for dashboard
const response = await fetch('/v1/model-health/stats', {
  headers: { 'Authorization': `Bearer ${apiKey}` }
});
const stats = await response.json();

// Display KPIs
console.log(`Total Models: ${stats.total_models}`);
console.log(`Success Rate: ${(stats.success_rate * 100).toFixed(2)}%`);
console.log(`Avg Response: ${Math.round(stats.average_response_time)}ms`);
```

### Use Case 2: Model Selection UI

```typescript
// Show health status in model dropdown
const response = await fetch(
  `/v1/model-health/openrouter/anthropic%2Fclaude-3-opus`,
  { headers: { 'Authorization': `Bearer ${apiKey}` } }
);
const health = await response.json();

const successRate = (health.success_count / health.call_count) * 100;
const statusIcon = successRate >= 95 ? '✅' : successRate >= 80 ? '⚠️' : '❌';

console.log(`${statusIcon} Claude 3 Opus - ${successRate.toFixed(1)}% uptime`);
```

### Use Case 3: Alert System

```typescript
// Check for unhealthy models
const response = await fetch(
  '/v1/model-health/unhealthy?error_threshold=0.2&min_calls=10',
  { headers: { 'Authorization': `Bearer ${apiKey}` } }
);
const data = await response.json();

if (data.total_unhealthy > 0) {
  showAlert(`${data.total_unhealthy} model(s) experiencing issues`);
}
```

### Use Case 4: Provider Comparison

```typescript
// Compare providers
const providers = ['openrouter', 'huggingface', 'together'];
const summaries = await Promise.all(
  providers.map(p =>
    fetch(`/v1/model-health/provider/${p}/summary`, {
      headers: { 'Authorization': `Bearer ${apiKey}` }
    }).then(r => r.json())
  )
);

// Sort by success rate
summaries.sort((a, b) => b.success_rate - a.success_rate);
console.log('Best performing provider:', summaries[0].provider);
```

---

## Webhooks (Future)

**Status**: Planned for future release

Webhook notifications for health events:
- Model health degraded
- Model recovered
- Provider-wide issues

---

## Changelog

### Version 1.0 (2025-11-24)
- Initial release
- 6 endpoints for health monitoring
- Real-time health tracking
- Provider-level statistics
- Unhealthy model detection

---

## Support

**Documentation**: See `docs/FRONTEND_MODEL_HEALTH_INTEGRATION.md`
**Quick Start**: See `docs/MODEL_HEALTH_QUICK_START.md`
**UI Mockups**: See `docs/MODEL_HEALTH_UI_MOCKUPS.md`

For API issues or questions:
- Check backend implementation: `src/routes/model_health.py`
- Review database schema: `supabase/migrations/20251121000001_add_model_health_tracking.sql`
- Contact backend team for support

---

**Last Updated**: 2025-11-24
**API Version**: v1
**Document Version**: 1.0
