# Admin Model Usage Analytics API

## Endpoint

```
GET /admin/model-usage-analytics
```

## Authentication

**Required:** Admin API key

This endpoint requires admin-level authentication. Include your admin API key in the request headers:

```bash
Authorization: Bearer <admin-api-key>
```

## Description

Retrieve comprehensive model usage analytics with pagination and search capabilities. This endpoint queries the `model_usage_analytics` view to provide detailed insights into model costs, usage, and performance.

## Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | No | 1 | Page number (must be ≥ 1) |
| `limit` | integer | No | 50 | Items per page (1-500) |
| `model_name` | string | No | - | Search by model name (partial, case-insensitive) |
| `sort_by` | string | No | `total_cost_usd` | Field to sort by (see valid fields below) |
| `sort_order` | string | No | `desc` | Sort order: `asc` or `desc` |

### Valid Sort Fields

- `model_name` - Model display name
- `provider_name` - Provider name
- `successful_requests` - Total successful request count
- `total_cost_usd` - Total cost in USD
- `avg_cost_per_request_usd` - Average cost per request
- `total_input_tokens` - Total input tokens used
- `total_output_tokens` - Total output tokens used
- `total_tokens` - Total tokens (input + output)
- `avg_processing_time_ms` - Average processing time
- `first_request_at` - Timestamp of first request
- `last_request_at` - Timestamp of last request

## Response Format

```json
{
  "success": true,
  "data": [
    {
      "model_id": 123,
      "model_name": "GPT-4",
      "model_identifier": "gpt-4",
      "provider_model_id": "gpt-4-0613",
      "provider_name": "OpenAI",
      "provider_slug": "openai",
      "successful_requests": 15420,
      "total_input_tokens": 2450000,
      "total_output_tokens": 850000,
      "total_tokens": 3300000,
      "avg_input_tokens_per_request": 158.87,
      "avg_output_tokens_per_request": 55.13,
      "input_token_price": 0.00003,
      "output_token_price": 0.00006,
      "total_cost_usd": 124.5,
      "input_cost_usd": 73.5,
      "output_cost_usd": 51.0,
      "avg_cost_per_request_usd": 0.008074,
      "avg_processing_time_ms": 2450.5,
      "context_length": 8192,
      "modality": "text->text",
      "health_status": "healthy",
      "is_active": true,
      "first_request_at": "2024-01-01T10:30:00Z",
      "last_request_at": "2024-01-14T15:45:30Z"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 50,
    "total_items": 342,
    "total_pages": 7,
    "has_next": true,
    "has_prev": false,
    "offset": 0
  },
  "filters": {
    "model_name": "gpt",
    "sort_by": "total_cost_usd",
    "sort_order": "desc"
  },
  "metadata": {
    "timestamp": "2024-01-14T12:00:00Z",
    "items_in_page": 50
  }
}
```

## Example Requests

### 1. Get all models (default pagination)

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 2. Search for GPT models

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?model_name=gpt" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 3. Get most expensive models (sorted by cost)

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?sort_by=total_cost_usd&sort_order=desc&limit=10" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 4. Get most popular models (sorted by requests)

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?sort_by=successful_requests&sort_order=desc&limit=10" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 5. Paginate through results

```bash
# Page 1
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?page=1&limit=100" \
  -H "Authorization: Bearer <admin-api-key>"

# Page 2
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?page=2&limit=100" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 6. Search and sort combined

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?model_name=claude&sort_by=avg_cost_per_request_usd&sort_order=asc" \
  -H "Authorization: Bearer <admin-api-key>"
```

### 7. Get slowest models (by processing time)

```bash
curl -X GET "https://api.gatewayz.ai/admin/model-usage-analytics?sort_by=avg_processing_time_ms&sort_order=desc&limit=20" \
  -H "Authorization: Bearer <admin-api-key>"
```

## Response Fields

### Model Identification
- `model_id` - Internal database ID
- `model_name` - Human-readable model name
- `model_identifier` - Model identifier string
- `provider_model_id` - Provider-specific model ID
- `provider_name` - Provider name (e.g., "OpenAI")
- `provider_slug` - Provider slug (e.g., "openai")

### Usage Metrics
- `successful_requests` - Total number of successful requests
- `total_input_tokens` - Sum of all input tokens
- `total_output_tokens` - Sum of all output tokens
- `total_tokens` - Sum of input + output tokens
- `avg_input_tokens_per_request` - Average input tokens per request
- `avg_output_tokens_per_request` - Average output tokens per request

### Pricing & Costs
- `input_token_price` - Cost per single input token (USD)
- `output_token_price` - Cost per single output token (USD)
- `total_cost_usd` - Total cost for all requests (USD)
- `input_cost_usd` - Cost from input tokens only (USD)
- `output_cost_usd` - Cost from output tokens only (USD)
- `avg_cost_per_request_usd` - Average cost per request (USD)

**Note:** Pricing is stored per single token. For example, GPT-4 at $30/1M = $0.00003 per token.

### Performance
- `avg_processing_time_ms` - Average request processing time in milliseconds

### Model Metadata
- `context_length` - Maximum context window size
- `modality` - Model modality (e.g., "text->text")
- `health_status` - Current health status
- `is_active` - Whether the model is currently active

### Timestamps
- `first_request_at` - When the first successful request was made
- `last_request_at` - When the most recent request was made

## Error Responses

### 401 Unauthorized
```json
{
  "detail": "Not authenticated"
}
```

### 403 Forbidden
```json
{
  "detail": "Admin access required"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Failed to get model usage analytics: <error details>"
}
```

## Use Cases

1. **Cost Analysis**: Find which models are most expensive
2. **Usage Tracking**: Identify most popular models
3. **Performance Monitoring**: Track average processing times
4. **Budget Planning**: Analyze costs per request and total spending
5. **Model Discovery**: Search for specific models by name
6. **Capacity Planning**: Monitor token usage trends

## Notes

- Data is sourced from the `model_usage_analytics` database view
- View is automatically updated as new requests complete
- Only includes models with at least 1 successful request
- Failed requests are excluded from calculations
- All costs use current model pricing (not historical)
- Maximum page size is 500 items
- Search is case-insensitive and supports partial matches

## Related Documentation

- [Model Usage Analytics View](../MODEL_USAGE_ANALYTICS.md)
- [Example SQL Queries](../queries/model_usage_analytics_examples.sql)
- [Pricing Service](../../src/services/pricing.py)
