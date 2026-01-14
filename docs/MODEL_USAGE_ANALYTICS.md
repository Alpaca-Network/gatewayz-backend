# Model Usage Analytics View

## Overview

The `model_usage_analytics` view provides comprehensive analytics for all models that have at least one successful request. It combines pricing data from the `models` table with actual usage data from the `chat_completion_requests` table to give you complete cost and usage insights.

## Quick Start

### Basic Query

```sql
SELECT * FROM model_usage_analytics
ORDER BY successful_requests DESC
LIMIT 10;
```

### Via Python

```python
from src.config.supabase_config import get_supabase_client

client = get_supabase_client()
result = client.table("model_usage_analytics").select("*").order("successful_requests", desc=True).limit(10).execute()

models = result.data
for model in models:
    print(f"{model['model_name']}: {model['successful_requests']} requests, ${model['total_cost_usd']} total")
```

## Columns Reference

### Model Identification
| Column | Type | Description |
|--------|------|-------------|
| `model_id` | INTEGER | Internal model ID |
| `model_name` | TEXT | Human-readable model name |
| `model_identifier` | TEXT | Model identifier (e.g., "gpt-4") |
| `provider_model_id` | TEXT | Provider-specific model ID |
| `provider_name` | TEXT | Provider name (e.g., "OpenAI") |
| `provider_slug` | TEXT | Provider slug (e.g., "openrouter") |

### Request Counts
| Column | Type | Description |
|--------|------|-------------|
| `successful_requests` | BIGINT | Total number of successful (completed) requests |

### Token Usage
| Column | Type | Description |
|--------|------|-------------|
| `total_input_tokens` | BIGINT | Sum of all input/prompt tokens |
| `total_output_tokens` | BIGINT | Sum of all output/completion tokens |
| `total_tokens` | BIGINT | Sum of input + output tokens |
| `avg_input_tokens_per_request` | NUMERIC | Average input tokens per request |
| `avg_output_tokens_per_request` | NUMERIC | Average output tokens per request |

### Pricing (Per 1 Million Tokens)
| Column | Type | Description |
|--------|------|-------------|
| `input_token_price_per_1m` | NUMERIC(20,10) | Price per 1M input tokens (USD) |
| `output_token_price_per_1m` | NUMERIC(20,10) | Price per 1M output tokens (USD) |

### Cost Calculations (USD)
| Column | Type | Description |
|--------|------|-------------|
| `total_cost_usd` | NUMERIC | Total cost (input + output) |
| `input_cost_usd` | NUMERIC | Cost from input tokens only |
| `output_cost_usd` | NUMERIC | Cost from output tokens only |
| `avg_cost_per_request_usd` | NUMERIC | Average cost per request |

**Formula:**
```
total_cost_usd = (total_input_tokens × input_token_price_per_1m / 1,000,000)
               + (total_output_tokens × output_token_price_per_1m / 1,000,000)
```

### Performance Metrics
| Column | Type | Description |
|--------|------|-------------|
| `avg_processing_time_ms` | NUMERIC | Average request processing time in milliseconds |

### Model Metadata
| Column | Type | Description |
|--------|------|-------------|
| `context_length` | INTEGER | Maximum context window size |
| `modality` | TEXT | Model modality (e.g., "text->text") |
| `health_status` | TEXT | Current health status |
| `is_active` | BOOLEAN | Whether the model is active |

### Time Tracking
| Column | Type | Description |
|--------|------|-------------|
| `first_request_at` | TIMESTAMP | When the first successful request was made |
| `last_request_at` | TIMESTAMP | When the most recent request was made |

## Common Use Cases

### 1. Total Spending by Model

```sql
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_cost_usd
FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;
```

### 2. Most Popular Models

```sql
SELECT
    model_name,
    provider_name,
    successful_requests,
    total_cost_usd,
    avg_cost_per_request_usd
FROM model_usage_analytics
ORDER BY successful_requests DESC
LIMIT 10;
```

### 3. Cost Per Request Analysis

```sql
SELECT
    model_name,
    provider_name,
    successful_requests,
    avg_cost_per_request_usd,
    avg_input_tokens_per_request,
    avg_output_tokens_per_request
FROM model_usage_analytics
WHERE successful_requests >= 10
ORDER BY avg_cost_per_request_usd DESC;
```

### 4. Provider Comparison

```sql
SELECT
    provider_name,
    COUNT(DISTINCT model_id) as model_count,
    SUM(successful_requests) as total_requests,
    ROUND(SUM(total_cost_usd), 2) as total_cost_usd,
    ROUND(AVG(avg_cost_per_request_usd), 6) as avg_cost_per_request
FROM model_usage_analytics
GROUP BY provider_name
ORDER BY total_cost_usd DESC;
```

### 5. Input vs Output Cost Breakdown

```sql
SELECT
    model_name,
    provider_name,
    input_cost_usd,
    output_cost_usd,
    total_cost_usd,
    ROUND((output_cost_usd / NULLIF(total_cost_usd, 0)) * 100, 1) as output_cost_percentage
FROM model_usage_analytics
WHERE total_cost_usd > 0.01
ORDER BY total_cost_usd DESC
LIMIT 20;
```

### 6. Token Efficiency

```sql
SELECT
    model_name,
    successful_requests,
    total_tokens,
    total_cost_usd,
    ROUND((total_cost_usd * 1000) / NULLIF(total_tokens, 0), 6) as cost_per_1k_tokens
FROM model_usage_analytics
WHERE total_tokens > 0
ORDER BY cost_per_1k_tokens ASC
LIMIT 20;
```

## Performance Considerations

The view is optimized with the following indexes:

```sql
-- On chat_completion_requests
CREATE INDEX idx_chat_completion_requests_model_id_status
    ON chat_completion_requests (model_id, status);

CREATE INDEX idx_chat_completion_requests_status_created_at
    ON chat_completion_requests (status, created_at);

-- On models
CREATE INDEX idx_models_pricing
    ON models (pricing_prompt, pricing_completion);
```

## Real-Time Updates

The view is **automatically updated** as new requests are completed. No manual refresh required.

## Filtering Examples

### By Provider
```sql
SELECT * FROM model_usage_analytics
WHERE provider_slug = 'openrouter'
ORDER BY total_cost_usd DESC;
```

### By Date Range (last 30 days)
```sql
SELECT * FROM model_usage_analytics
WHERE last_request_at >= NOW() - INTERVAL '30 days'
ORDER BY successful_requests DESC;
```

### By Cost Threshold
```sql
SELECT * FROM model_usage_analytics
WHERE total_cost_usd >= 1.00  -- Models with at least $1 in costs
ORDER BY total_cost_usd DESC;
```

### Active Models Only
```sql
SELECT * FROM model_usage_analytics
WHERE is_active = true
ORDER BY successful_requests DESC;
```

## Migration Details

**Migration File:** `supabase/migrations/20260114000000_add_model_usage_analytics_view.sql`

**Applied:** Automatically via Supabase migrations

**Rollback:**
```sql
DROP VIEW IF EXISTS model_usage_analytics;
```

## Related Documentation

- See `docs/queries/model_usage_analytics_examples.sql` for 15+ example queries
- See `src/services/pricing.py` for pricing calculation logic
- See `src/db/chat_completion_requests.py` for usage tracking

## Notes

- ✅ Only includes models with at least 1 successful request
- ✅ Failed requests are excluded from cost calculations
- ✅ Costs are calculated using the model's current pricing (not historical)
- ✅ Pricing is per 1 million tokens (industry standard)
- ✅ All monetary values are in USD

## Support

For questions or issues with this view, contact the backend team or create an issue on GitHub.
