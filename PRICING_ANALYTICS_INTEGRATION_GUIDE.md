## Pricing Analytics Integration Guide

This guide explains how to integrate the pricing calculator with admin monitoring analytics to track token usage and costs per model.

## Overview

The system has three main components:

1. **Pricing Calculator** (`pricing_calculator.py`) - Normalizes all provider pricing formats
2. **Cost Tracking** (`chat_completion_requests` table) - Stores per-request costs
3. **Analytics** (`pricing_analytics.py` + admin routes) - Aggregates and analyzes cost data

## Architecture

```
┌─────────────────┐
│  Chat Request   │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  chat.py (src/routes/chat.py)           │
│  1. Process request                     │
│  2. Calculate cost using pricing_calc   │
│  3. Save to chat_completion_requests    │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Database (chat_completion_requests)    │
│  - request_id, model_id                 │
│  - input_tokens, output_tokens          │
│  - cost_usd, input_cost_usd,            │
│    output_cost_usd                      │
│  - pricing_source                       │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Analytics View (model_usage_analytics) │
│  Aggregates costs by model:             │
│  - Total cost per model                 │
│  - Average cost per request             │
│  - Token usage stats                    │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Admin API (admin_pricing_analytics.py) │
│  GET /admin/pricing-analytics/models    │
│  GET /admin/pricing-analytics/summary   │
│  GET /admin/pricing-analytics/trend     │
└─────────────────────────────────────────┘
```

## Step 1: Run Database Migration

First, add cost tracking columns to the `chat_completion_requests` table:

```bash
# Apply the migration
supabase db push

# Or run manually
psql $DATABASE_URL -f supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql
```

This adds:
- `cost_usd` - Total cost in USD
- `input_cost_usd` - Cost for input tokens
- `output_cost_usd` - Cost for output tokens
- `pricing_source` - Source of pricing data

## Step 2: Integrate Pricing Calculator in Chat Route

Update `src/routes/chat.py` to save cost data when processing requests.

### Current Code (Line ~574)

```python
cost = calculate_cost(model, prompt_tokens, completion_tokens)
```

### Enhanced Code

```python
# Import the enhanced save function
from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost

# Import pricing calculator
from pricing_calculator import calculate_model_cost

# ... in the request handler ...

# Calculate cost using the pricing calculator for accurate provider-specific calculation
model_data = {
    "id": model,
    "architecture": {"modality": "text->text"},  # Or get from model catalog
    "pricing": {
        "prompt": str(prompt_price_per_token),    # From model pricing
        "completion": str(completion_price_per_token)
    }
}

usage = {
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens
}

# Calculate detailed cost breakdown
cost_breakdown = calculate_model_cost(provider_name, model_data, usage)

total_cost = cost_breakdown.get("total_cost", 0.0)
input_cost = cost_breakdown.get("prompt_cost", 0.0)
output_cost = cost_breakdown.get("completion_cost", 0.0)

# Save request with cost information
save_chat_completion_request_with_cost(
    request_id=request_id,
    model_name=model,
    input_tokens=prompt_tokens,
    output_tokens=completion_tokens,
    processing_time_ms=int(elapsed_ms),
    cost_usd=total_cost,
    input_cost_usd=input_cost,
    output_cost_usd=output_cost,
    pricing_source="pricing_calculator",
    status="completed",
    user_id=user.get("id") if not is_anonymous else None,
    provider_name=provider_name,
    api_key_id=api_key_id,
    is_anonymous=is_anonymous
)
```

### Minimal Integration (Fallback to existing cost calculation)

If you want a simpler integration using the existing `calculate_cost` function:

```python
from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost

# ... existing code ...
cost = calculate_cost(model, prompt_tokens, completion_tokens)

# Calculate breakdown (simple approach)
prompt_price = get_model_pricing(model)["prompt"]
completion_price = get_model_pricing(model)["completion"]
input_cost = prompt_tokens * prompt_price
output_cost = completion_tokens * completion_price

# Save with cost
save_chat_completion_request_with_cost(
    request_id=request_id,
    model_name=model,
    input_tokens=prompt_tokens,
    output_tokens=completion_tokens,
    processing_time_ms=int(elapsed_ms),
    cost_usd=cost,
    input_cost_usd=input_cost,
    output_cost_usd=output_cost,
    pricing_source="calculated",
    # ... rest of params ...
)
```

## Step 3: Register Admin Analytics Routes

Add the pricing analytics routes to your FastAPI app in `src/main.py`:

```python
# Import the router
from src.routes.admin_pricing_analytics import router as admin_pricing_router

# Register the router
app.include_router(admin_pricing_router)
```

## Step 4: Backfill Historical Data (Optional)

If you have existing requests without cost data:

```python
from src.db.chat_completion_requests_enhanced import backfill_request_costs

# Backfill in batches
for offset in range(0, 10000, 1000):
    result = backfill_request_costs(limit=1000, offset=offset)
    print(f"Processed: {result['processed']}, Updated: {result['updated']}, Cost: ${result['total_cost_calculated']}")
```

Or use the SQL function directly:

```sql
SELECT * FROM calculate_missing_request_costs();
```

## Admin API Endpoints

### 1. Get Model Analytics

```bash
GET /admin/pricing-analytics/models?time_range=30d&sort_by=cost&limit=50
```

Response:
```json
{
  "models": [
    {
      "model_id": 123,
      "model_name": "GPT-4",
      "provider_slug": "openrouter",
      "successful_requests": 1500,
      "total_input_tokens": 150000,
      "total_output_tokens": 75000,
      "total_cost_usd": 9.0,
      "input_cost_usd": 4.5,
      "output_cost_usd": 4.5,
      "avg_cost_per_request_usd": 0.006
    }
  ],
  "summary": {
    "total_models": 50,
    "total_cost_usd": 1250.50,
    "total_requests": 125000,
    "total_tokens": 15000000
  }
}
```

### 2. Get Provider Cost Breakdown

```bash
GET /admin/pricing-analytics/providers?time_range=30d
```

Response:
```json
[
  {
    "provider_slug": "openrouter",
    "provider_name": "OpenRouter",
    "model_count": 25,
    "total_requests": 50000,
    "total_tokens": 10000000,
    "total_cost_usd": 850.50,
    "avg_cost_per_request": 0.017
  }
]
```

### 3. Get Cost Trend

```bash
GET /admin/pricing-analytics/trend?granularity=day&time_range=7d
```

Response:
```json
[
  {
    "time_bucket": "2026-01-08T00:00:00Z",
    "request_count": 5000,
    "total_tokens": 1000000,
    "total_cost": 50.25,
    "input_cost": 25.12,
    "output_cost": 25.13
  },
  {
    "time_bucket": "2026-01-09T00:00:00Z",
    "request_count": 5500,
    "total_tokens": 1100000,
    "total_cost": 55.27,
    "input_cost": 27.63,
    "output_cost": 27.64
  }
]
```

### 4. Get Most Expensive Models

```bash
GET /admin/pricing-analytics/expensive-models?limit=10&time_range=30d
```

### 5. Get Most Popular Models

```bash
GET /admin/pricing-analytics/popular-models?limit=10&time_range=30d
```

### 6. Get Efficiency Report

```bash
GET /admin/pricing-analytics/efficiency-report?time_range=30d
```

Response:
```json
{
  "summary": {
    "total_cost_usd": 1250.50,
    "total_requests": 125000
  },
  "efficiency_metrics": {
    "avg_cost_per_request": 0.010004,
    "avg_cost_per_token": 0.000000083,
    "most_efficient_models": [...],
    "least_efficient_models": [...]
  },
  "recommendations": [
    {
      "type": "high_cost_model",
      "message": "Consider alternatives to GPT-4...",
      "model": "GPT-4",
      "current_cost": 0.06
    }
  ]
}
```

### 7. Get Quick Summary

```bash
GET /admin/pricing-analytics/summary?time_range=30d
```

Returns a comprehensive dashboard summary with all key metrics.

## Database Views

### model_usage_analytics View

Automatically aggregates cost data per model:

```sql
SELECT * FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;
```

Columns:
- `model_id`, `model_name`, `model_identifier`
- `provider_name`, `provider_slug`
- `successful_requests`
- `total_input_tokens`, `total_output_tokens`, `total_tokens`
- `avg_input_tokens_per_request`, `avg_output_tokens_per_request`
- `input_token_price`, `output_token_price`
- `total_cost_usd`, `input_cost_usd`, `output_cost_usd`
- `avg_cost_per_request_usd`
- `avg_processing_time_ms`
- `first_request_at`, `last_request_at`

## Monitoring Queries

### Top 10 Most Expensive Models

```sql
SELECT
    model_name,
    provider_slug,
    total_cost_usd,
    successful_requests,
    avg_cost_per_request_usd
FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;
```

### Daily Cost Trend (Last 30 Days)

```sql
SELECT
    DATE(created_at) as date,
    COUNT(*) as requests,
    SUM(cost_usd) as total_cost,
    AVG(cost_usd) as avg_cost_per_request
FROM chat_completion_requests
WHERE status = 'completed'
AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;
```

### Cost by Provider (This Month)

```sql
SELECT
    p.slug as provider,
    COUNT(ccr.id) as requests,
    SUM(ccr.cost_usd) as total_cost,
    AVG(ccr.cost_usd) as avg_cost
FROM chat_completion_requests ccr
JOIN models m ON ccr.model_id = m.id
JOIN providers p ON m.provider_id = p.id
WHERE ccr.status = 'completed'
AND ccr.created_at >= DATE_TRUNC('month', NOW())
GROUP BY p.slug
ORDER BY total_cost DESC;
```

### Users with Highest Costs

```sql
SELECT
    u.id,
    u.email,
    COUNT(ccr.id) as requests,
    SUM(ccr.cost_usd) as total_cost,
    AVG(ccr.cost_usd) as avg_cost_per_request
FROM chat_completion_requests ccr
JOIN users u ON ccr.user_id = u.id
WHERE ccr.status = 'completed'
AND ccr.created_at >= NOW() - INTERVAL '30 days'
GROUP BY u.id, u.email
ORDER BY total_cost DESC
LIMIT 20;
```

## Frontend Integration Example

### React Dashboard Component

```typescript
import { useState, useEffect } from 'react';

function PricingAnalyticsDashboard() {
  const [analytics, setAnalytics] = useState(null);
  const [timeRange, setTimeRange] = useState('30d');

  useEffect(() => {
    fetch(`/admin/pricing-analytics/summary?time_range=${timeRange}`, {
      headers: { 'Authorization': `Bearer ${adminToken}` }
    })
      .then(res => res.json())
      .then(data => setAnalytics(data));
  }, [timeRange]);

  if (!analytics) return <div>Loading...</div>;

  return (
    <div className="dashboard">
      <h1>Pricing Analytics</h1>

      {/* Time Range Selector */}
      <select value={timeRange} onChange={e => setTimeRange(e.target.value)}>
        <option value="1h">Last Hour</option>
        <option value="24h">Last 24 Hours</option>
        <option value="7d">Last 7 Days</option>
        <option value="30d">Last 30 Days</option>
      </select>

      {/* Summary Cards */}
      <div className="summary-cards">
        <div className="card">
          <h3>Total Cost</h3>
          <p>${analytics.summary.total_cost_usd.toFixed(2)}</p>
        </div>
        <div className="card">
          <h3>Total Requests</h3>
          <p>{analytics.summary.total_requests.toLocaleString()}</p>
        </div>
        <div className="card">
          <h3>Total Tokens</h3>
          <p>{analytics.summary.total_tokens.toLocaleString()}</p>
        </div>
      </div>

      {/* Top Expensive Models */}
      <div className="expensive-models">
        <h2>Most Expensive Models</h2>
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Provider</th>
              <th>Requests</th>
              <th>Total Cost</th>
            </tr>
          </thead>
          <tbody>
            {analytics.top_expensive_models.map(model => (
              <tr key={model.model_id}>
                <td>{model.model_name}</td>
                <td>{model.provider_slug}</td>
                <td>{model.successful_requests}</td>
                <td>${model.total_cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Cost by Provider Chart */}
      <div className="provider-breakdown">
        <h2>Cost by Provider</h2>
        {analytics.cost_by_provider.map(provider => (
          <div key={provider.provider_slug} className="provider-bar">
            <span>{provider.provider_name}</span>
            <div className="bar" style={{
              width: `${(provider.total_cost_usd / analytics.summary.total_cost_usd) * 100}%`
            }} />
            <span>${provider.total_cost_usd.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

## Performance Optimization

### Indexes

The migration creates these indexes for fast queries:

```sql
-- Cost-based queries
CREATE INDEX idx_chat_completion_requests_cost
ON chat_completion_requests (cost_usd DESC NULLS LAST)
WHERE cost_usd IS NOT NULL;

-- Model + cost queries
CREATE INDEX idx_chat_completion_requests_model_cost
ON chat_completion_requests (model_id, cost_usd DESC NULLS LAST)
WHERE cost_usd IS NOT NULL;

-- Model + status queries
CREATE INDEX idx_chat_completion_requests_model_id_status
ON chat_completion_requests (model_id, status);

-- Time-based queries
CREATE INDEX idx_chat_completion_requests_status_created_at
ON chat_completion_requests (status, created_at);
```

### Materialized Views (Optional)

For very high traffic, consider materialized views:

```sql
CREATE MATERIALIZED VIEW daily_model_costs AS
SELECT
    DATE(created_at) as date,
    model_id,
    COUNT(*) as requests,
    SUM(cost_usd) as total_cost,
    SUM(input_tokens) as input_tokens,
    SUM(output_tokens) as output_tokens
FROM chat_completion_requests
WHERE status = 'completed'
GROUP BY DATE(created_at), model_id;

-- Refresh daily
REFRESH MATERIALIZED VIEW CONCURRENTLY daily_model_costs;
```

## Troubleshooting

### Issue: Cost not being saved

**Check:**
1. Migration applied? `SELECT * FROM information_schema.columns WHERE table_name = 'chat_completion_requests' AND column_name = 'cost_usd';`
2. Model pricing exists? `SELECT pricing_prompt, pricing_completion FROM models WHERE id = ?;`
3. Logs for errors? Check application logs for save errors

### Issue: Analytics showing zero cost

**Check:**
1. `cost_usd` column populated? `SELECT COUNT(*) FROM chat_completion_requests WHERE cost_usd IS NOT NULL;`
2. Run backfill if needed: `SELECT * FROM calculate_missing_request_costs();`

### Issue: Slow analytics queries

**Solutions:**
1. Ensure indexes are created
2. Use time range filters
3. Limit result sets
4. Consider materialized views for heavy aggregations

## Summary

You now have a complete pricing analytics system that:

✅ Tracks costs per request with provider-specific pricing
✅ Stores cost breakdown (input/output)
✅ Aggregates costs by model, provider, and time
✅ Provides admin API endpoints for monitoring
✅ Supports trend analysis and efficiency reports
✅ Includes cost optimization recommendations

The system automatically normalizes all provider pricing formats and accurately calculates costs for every request!
