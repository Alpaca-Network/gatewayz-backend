# Complete Pricing Tracking & Analytics System

This system provides comprehensive tracking of token usage and costs per model with admin monitoring capabilities.

## 📦 What You Have

### 1. Pricing Standards & Calculator
- **`provider_pricing_standards.json`** - Pricing format for all 13 providers
- **`pricing_calculator.py`** - Universal cost calculator that handles all provider formats
- **`PRICING_CALCULATOR_GUIDE.md`** - Complete usage documentation

### 2. Database Schema
- **`supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql`**
  - Adds cost tracking columns to `chat_completion_requests` table
  - Includes backfill function for historical data
  - Creates optimized indexes

- **Existing: `model_usage_analytics` view**
  - Automatically aggregates costs by model
  - Real-time analytics

### 3. Services & Database Layers
- **`src/services/pricing_analytics.py`** - Analytics service
  - `get_model_usage_analytics()` - Get cost analytics per model
  - `get_cost_breakdown_by_provider()` - Aggregate by provider
  - `get_cost_trend()` - Time-series cost data
  - `get_most_expensive_models()` - Top expensive models
  - `get_most_used_models()` - Top popular models
  - `get_pricing_efficiency_report()` - Efficiency analysis & recommendations

- **`src/db/chat_completion_requests_enhanced.py`** - Enhanced DB operations
  - `save_chat_completion_request_with_cost()` - Save with cost breakdown
  - `update_request_cost()` - Update cost data
  - `backfill_request_costs()` - Backfill historical costs
  - `get_requests_with_cost()` - Query with filtering

### 4. Admin API Routes
- **`src/routes/admin_pricing_analytics.py`** - 7 analytics endpoints
  - `GET /admin/pricing-analytics/models` - Model analytics
  - `GET /admin/pricing-analytics/providers` - Provider breakdown
  - `GET /admin/pricing-analytics/trend` - Cost trend over time
  - `GET /admin/pricing-analytics/expensive-models` - Top costly models
  - `GET /admin/pricing-analytics/popular-models` - Top used models
  - `GET /admin/pricing-analytics/efficiency-report` - Efficiency analysis
  - `GET /admin/pricing-analytics/summary` - Dashboard summary

### 5. Documentation & Integration
- **`PRICING_ANALYTICS_INTEGRATION_GUIDE.md`** - Complete integration guide
- **`INTEGRATION_EXAMPLE.py`** - Exact code examples for chat.py
- **`provider_pricing_schema.sql`** - Optional database schema for standards

## 🚀 Quick Start (3 Steps)

### Step 1: Run Database Migration

```bash
# Apply migration
supabase db push

# Or manually
psql $DATABASE_URL -f supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql
```

This adds 4 columns to `chat_completion_requests`:
- `cost_usd` - Total cost
- `input_cost_usd` - Input cost
- `output_cost_usd` - Output cost
- `pricing_source` - Data source

### Step 2: Integrate in chat.py

Add to imports:
```python
from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
```

Replace save call (around line ~662):
```python
# Old:
# save_chat_completion_request(...)

# New:
save_chat_completion_request_with_cost(
    request_id=request_id,
    model_name=model,
    input_tokens=prompt_tokens,
    output_tokens=completion_tokens,
    processing_time_ms=int(elapsed_ms),
    cost_usd=cost,
    input_cost_usd=prompt_tokens * prompt_price,
    output_cost_usd=completion_tokens * completion_price,
    pricing_source="calculated",
    status="completed",
    user_id=user.get("id") if user else None,
    provider_name=provider,
    api_key_id=api_key_id,
    is_anonymous=is_anonymous
)
```

See `INTEGRATION_EXAMPLE.py` for complete code examples.

### Step 3: Register Admin Routes

In `src/main.py`:
```python
from src.routes.admin_pricing_analytics import router as admin_pricing_router

# In create_app():
app.include_router(admin_pricing_router)
```

## 📊 Usage Examples

### API Calls

```bash
# Get overall summary
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.gatewayz.ai/admin/pricing-analytics/summary?time_range=30d

# Get model analytics
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.gatewayz.ai/admin/pricing-analytics/models?sort_by=cost&limit=20

# Get provider breakdown
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.gatewayz.ai/admin/pricing-analytics/providers

# Get cost trend
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.gatewayz.ai/admin/pricing-analytics/trend?granularity=day&time_range=7d

# Get efficiency report
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.gatewayz.ai/admin/pricing-analytics/efficiency-report
```

### Database Queries

```sql
-- View aggregated analytics
SELECT * FROM model_usage_analytics
ORDER BY total_cost_usd DESC
LIMIT 10;

-- Daily cost trend
SELECT
    DATE(created_at) as date,
    COUNT(*) as requests,
    SUM(cost_usd) as total_cost,
    AVG(cost_usd) as avg_cost
FROM chat_completion_requests
WHERE status = 'completed'
AND created_at >= NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- Cost by provider
SELECT
    p.slug,
    COUNT(ccr.id) as requests,
    SUM(ccr.cost_usd) as total_cost
FROM chat_completion_requests ccr
JOIN models m ON ccr.model_id = m.id
JOIN providers p ON m.provider_id = p.id
WHERE ccr.status = 'completed'
GROUP BY p.slug
ORDER BY total_cost DESC;
```

### Python API

```python
from src.services.pricing_analytics import (
    get_model_usage_analytics,
    get_cost_breakdown_by_provider,
    get_pricing_efficiency_report
)

# Get analytics
analytics = get_model_usage_analytics(time_range="30d", limit=50)
print(f"Total cost: ${analytics['summary']['total_cost_usd']:.2f}")

# Get provider breakdown
providers = get_cost_breakdown_by_provider(time_range="30d")
for p in providers:
    print(f"{p['provider_name']}: ${p['total_cost_usd']:.2f}")

# Get efficiency report
report = get_pricing_efficiency_report(time_range="30d")
print(f"Avg cost per request: ${report['efficiency_metrics']['avg_cost_per_request']:.6f}")
```

## 🔧 Features

### ✅ Cost Tracking
- Per-request cost tracking
- Input/output cost breakdown
- Pricing source attribution
- Supports all 13 providers

### ✅ Analytics
- Aggregate by model, provider, time
- Trend analysis
- Efficiency metrics
- Cost optimization recommendations

### ✅ Admin Monitoring
- Real-time analytics view
- Historical cost data
- Customizable time ranges
- Export capabilities

### ✅ Performance
- Optimized indexes
- Materialized views support
- Batch processing
- Efficient aggregations

## 📈 Example Dashboard Data

```json
{
  "summary": {
    "total_cost_usd": 1250.50,
    "total_requests": 125000,
    "total_tokens": 15000000,
    "total_models": 50
  },
  "top_expensive_models": [
    {
      "model_name": "GPT-4",
      "total_cost_usd": 600.00,
      "successful_requests": 10000,
      "avg_cost_per_request_usd": 0.06
    }
  ],
  "top_popular_models": [
    {
      "model_name": "Llama-3-8B",
      "successful_requests": 50000,
      "total_cost_usd": 50.00
    }
  ],
  "cost_by_provider": [
    {
      "provider_slug": "openrouter",
      "total_cost_usd": 850.50,
      "model_count": 25,
      "total_requests": 50000
    }
  ]
}
```

## 🔍 Troubleshooting

### No cost data showing?

1. **Check migration applied:**
   ```sql
   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'chat_completion_requests'
   AND column_name = 'cost_usd';
   ```

2. **Backfill historical data:**
   ```sql
   SELECT * FROM calculate_missing_request_costs();
   ```

3. **Verify pricing data:**
   ```sql
   SELECT pricing_prompt, pricing_completion FROM models LIMIT 5;
   ```

### Analytics showing zero?

1. **Check for completed requests:**
   ```sql
   SELECT COUNT(*) FROM chat_completion_requests
   WHERE status = 'completed' AND cost_usd IS NOT NULL;
   ```

2. **Check date range:**
   - Ensure your time_range parameter includes data

3. **Check provider filter:**
   - Verify provider_slug is correct

### Slow queries?

1. **Verify indexes:**
   ```sql
   SELECT * FROM pg_indexes
   WHERE tablename = 'chat_completion_requests';
   ```

2. **Use time range filters**
3. **Limit result sets**
4. **Consider materialized views for heavy aggregations**

## 📚 Additional Resources

- **`PRICING_CALCULATOR_GUIDE.md`** - How to use the pricing calculator
- **`PRICING_ANALYTICS_INTEGRATION_GUIDE.md`** - Detailed integration guide
- **`INTEGRATION_EXAMPLE.py`** - Code examples
- **`provider_pricing_standards.json`** - All provider pricing formats
- **`provider_pricing_schema.sql`** - Optional database schema

## 🎯 Summary

You now have a complete system to:

✅ **Track** - Every request's cost with full breakdown
✅ **Analyze** - Aggregate by model, provider, time
✅ **Monitor** - Admin dashboard with real-time analytics
✅ **Optimize** - Identify expensive models and efficiency gaps
✅ **Report** - Export data for business intelligence

The system automatically handles all provider pricing formats and provides accurate cost tracking for every AI model request!

## 🚦 Next Steps

1. ✅ Run database migration
2. ✅ Integrate in chat.py route
3. ✅ Register admin routes
4. 📊 Build frontend dashboard (see guide)
5. 📈 Set up alerts for cost thresholds
6. 🔄 Schedule regular cost reports

---

**Need Help?** See the integration guides or check the example files for detailed code samples.
