# Grafana Endpoints & Schema Mapping
**Created:** 2025-12-28
**Purpose:** Endpoint URLs and response schemas for each Grafana dashboard
**Status:** 🔗 Ready for Integration

---

## Quick Reference Table

| Dashboard | Endpoints | Count | Data Source |
|-----------|-----------|-------|-------------|
| Executive Overview | Health, Real-time Stats, Metrics | 3 | Monitoring API + Prometheus |
| Model Performance | Models, Trending, Error Rates, Latency | 4 | Monitoring API + Catalog API |
| Gateway Comparison | Gateway Stats, Provider Stats | 2 | Catalog API + Monitoring API |
| Business Metrics | Cost Analysis, Trial Analytics | 2 | Monitoring API |
| Incident Response | Anomalies, Errors, Health, Circuit Breakers | 4 | Monitoring API |
| Tokens & Throughput | Real-time Stats (tokens field) | 1 | Monitoring API |

---

## Dashboard 1: Executive Overview

### Panel 1: Overall Health Score
**Source Endpoint:**
```
GET /api/monitoring/health
```

**Query Parameters:**
```
None
```

**Response Schema:**
```json
[
  {
    "provider": "string",          // e.g., "openrouter"
    "health_score": "number",      // 0-100, e.g., 98.5
    "status": "string",            // "healthy" | "degraded" | "down"
    "last_updated": "ISO-8601"     // e.g., "2025-12-28T23:17:40Z"
  },
  ...
]
```

**Data Transformation:**
```javascript
// Calculate overall health as average
const overallHealth = providers.reduce((sum, p) => sum + p.health_score, 0) / providers.length
```

**Grafana Panel Type:** `Stat` with Gauge
**Field to Use:** `health_score` (averaged)

---

### Panel 2: Active Requests per Minute
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=1
```

**Query Parameters:**
```
hours=1
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "providers": {
    "openrouter": {
      "total_requests": "integer",     // e.g., 1250
      "total_cost": "number",          // e.g., 45.67
      "health_score": "number",        // 0-100
      "error_rate": "number",          // 0-1 (0.02 = 2%)
      "avg_latency_ms": "integer",     // e.g., 245
      "hourly_breakdown": {
        "2025-12-28T23:00": {
          "requests": "integer",
          "cost": "number",
          "errors": "integer",
          "avg_latency_ms": "integer"
        }
      }
    },
    ...
  },
  "total_requests": "integer",         // e.g., 2596
  "total_cost": "number",              // e.g., 90.46
  "avg_health_score": "number"         // 0-100
}
```

**Data Transformation:**
```javascript
// Calculate requests per minute from total_requests in 1 hour
const requestsPerMinute = response.total_requests / 60
```

**Grafana Panel Type:** `Stat` with Big Number & Sparkline
**Field to Use:** `total_requests` → divide by 60 for per-minute

---

### Panel 3: Average Response Time
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=1
```

**Response Schema:** (same as Panel 2)

**Data Transformation:**
```javascript
// Calculate weighted average latency across all providers
const avgLatency = Object.values(response.providers)
  .reduce((sum, p) => sum + (p.avg_latency_ms * p.total_requests), 0) /
  Object.values(response.providers).reduce((sum, p) => sum + p.total_requests, 0)
```

**Grafana Panel Type:** `Stat` with Unit `ms`
**Field to Use:** `avg_latency_ms` (weighted average)

---

### Panel 4: Daily Cost
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (same as Panel 2)

**Data Transformation:**
```javascript
// Use total_cost from 24h window
const dailyCost = response.total_cost
```

**Grafana Panel Type:** `Stat` with Unit `currencyUSD`
**Field to Use:** `total_cost`

---

### Panel 5: Provider Health Grid
**Source Endpoint:**
```
GET /api/monitoring/health
```

**Response Schema:** (see Panel 1)

**Data Transformation:**
```javascript
// Map each provider to health status
providers.map(p => ({
  name: p.provider,
  value: p.health_score,
  status: p.status  // for color coding
}))
```

**Grafana Panel Type:** `Status Indicator Grid` (6 columns)
**Fields to Use:** `provider`, `health_score`, `status`

**Color Mapping:**
- `healthy` (health_score 90-100) → Green
- `degraded` (health_score 70-89) → Orange
- `down` (health_score < 70) → Red

---

### Panel 6: Request Volume (24h)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (see Panel 2, includes hourly_breakdown)

**Data Transformation:**
```javascript
// Extract hourly data for time series
const timeSeries = Object.entries(response.providers.openrouter.hourly_breakdown)
  .map(([timestamp, data]) => ({
    time: timestamp,
    value: data.requests
  }))
```

**Grafana Panel Type:** `Time Series` Line Chart
**Fields to Use:** `hourly_breakdown[*].requests`
**X-Axis:** Time (1h granularity)
**Y-Axis:** Request count

---

### Panel 7: Error Rate Distribution
**Source Endpoint:**
```
GET /api/monitoring/error-rates?hours=24
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "error_rates": {
    "openrouter": {
      "gpt-4o": {
        "total_errors": "integer",      // e.g., 25
        "total_requests": "integer",    // e.g., 1250
        "error_rate": "number",         // 0-1 (0.02 = 2%)
        "trend": "string"               // "stable" | "increasing" | "critical"
      },
      ...
    },
    ...
  }
}
```

**Data Transformation:**
```javascript
// Aggregate error rates by provider
const errorByProvider = {};
for (const [provider, models] of Object.entries(response.error_rates)) {
  const totalErrors = Object.values(models)
    .reduce((sum, m) => sum + m.total_errors, 0);
  const totalRequests = Object.values(models)
    .reduce((sum, m) => sum + m.total_requests, 0);
  errorByProvider[provider] = totalErrors / totalRequests;
}
```

**Grafana Panel Type:** `Pie Chart`
**Fields to Use:** Provider name → Error Rate %

---

### Panel 8: Critical Anomalies Alert List
**Source Endpoint:**
```
GET /api/monitoring/anomalies
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "anomalies": [
    {
      "type": "string",                // "cost_spike" | "latency_spike" | "high_error_rate"
      "provider": "string",            // e.g., "together"
      "hour": "ISO-8601",              // e.g., "2025-12-28T20:00:00Z"
      "value": "number",               // Current value
      "expected": "number",            // Expected/baseline value
      "severity": "string"             // "info" | "warning" | "critical"
    },
    ...
  ],
  "total_count": "integer",
  "critical_count": "integer",
  "warning_count": "integer"
}
```

**Data Transformation:**
```javascript
// Filter for critical/warning only, sort by severity
const alerts = response.anomalies
  .filter(a => a.severity !== "info")
  .sort((a, b) => {
    const severityOrder = { critical: 0, warning: 1 };
    return severityOrder[a.severity] - severityOrder[b.severity];
  })
```

**Grafana Panel Type:** `Alert List`
**Fields to Use:** `type`, `provider`, `severity`, `value`, `expected`

---

## Dashboard 2: Model Performance Analytics

### Panel 1: Top 5 Models by Requests (Table)
**Source Endpoint:**
```
GET /v1/models/trending?limit=10
```

**Query Parameters:**
```
limit=10
sort_by=requests
```

**Response Schema:**
```json
{
  "success": true,
  "data": [
    {
      "model": "string",               // e.g., "gpt-4o"
      "provider": "string",            // e.g., "openai"
      "requests": "integer",           // e.g., 5234
      "total_tokens": "integer",       // e.g., 567890
      "unique_users": "integer",       // e.g., 234
      "total_cost": "number",          // e.g., 123.45
      "avg_speed": "number",           // tokens/sec, e.g., 231.64
      "gateway": "string"              // e.g., "openrouter"
    },
    ...
  ],
  "count": "integer",
  "gateway": "string",
  "time_range": "string",              // "24h" | "7d" | etc.
  "timestamp": "ISO-8601"
}
```

**Data Transformation:**
```javascript
// Take top 5 and format for table
const topModels = response.data.slice(0, 5).map(m => ({
  rank: response.data.indexOf(m) + 1,
  model: m.model,
  provider: m.provider,
  requests: m.requests,
  cost: m.total_cost,
  costPerRequest: (m.total_cost / m.requests).toFixed(4)
}))
```

**Grafana Panel Type:** `Table` (sortable)
**Columns:** Rank, Model, Provider, Requests, Cost, Cost/Request

---

### Panel 2: Models with Issues (Table)
**Source Endpoint:**
```
GET /api/monitoring/error-rates?hours=24
```

**Response Schema:** (see Dashboard 1, Panel 7)

**Data Transformation:**
```javascript
// Filter models with error_rate > 5% (or >= 5%)
const problemModels = [];
for (const [provider, models] of Object.entries(response.error_rates)) {
  for (const [modelName, stats] of Object.entries(models)) {
    if (stats.error_rate >= 0.05) {
      problemModels.push({
        model: modelName,
        provider: provider,
        error_rate: (stats.error_rate * 100).toFixed(2) + '%',
        trend: stats.trend,
        total_errors: stats.total_errors
      });
    }
  }
}
// Sort by error rate descending
problemModels.sort((a, b) => parseFloat(b.error_rate) - parseFloat(a.error_rate));
```

**Grafana Panel Type:** `Table` (with color-coded trend)
**Columns:** Model, Provider, Error Rate %, Trend, Error Count

**Color Coding for Trend:**
- `critical` → Red
- `increasing` → Orange
- `stable` → Gray

---

### Panel 3: Request Volume by Model (7d)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=168
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Aggregate requests by model from hourly breakdown
// Note: This endpoint doesn't directly have model breakdown
// Alternative: Use v1/models/trending with larger time window
// OR reconstruct from /api/monitoring/latency-trends/{provider}
```

**Alternative Endpoint:**
```
GET /v1/models/trending?limit=20&time_range=7d
```

**Grafana Panel Type:** `Bar Chart` (stacked by provider)
**Fields to Use:** Model name → Request count (stacked)

---

### Panel 4: Cost per Request (Ranked)
**Source Endpoint:**
```
GET /api/monitoring/cost-analysis?days=7
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "period": "string",                  // "last_7_days"
  "total_cost": "number",              // e.g., 1245.67
  "by_provider": {
    "openrouter": {
      "total_cost": "number",          // e.g., 567.89
      "percentage": "number",          // 45.6 (%)
      "requests": "integer",           // e.g., 12450
      "cost_per_request": "number"     // e.g., 0.0456
    },
    ...
  },
  "most_expensive_model": {
    "model": "string",                 // e.g., "gpt-4o"
    "provider": "string",              // e.g., "openrouter"
    "cost": "number",                  // e.g., 345.23
    "requests": "integer"              // e.g., 5670
  }
}
```

**Data Transformation:**
```javascript
// Create ranking from cost_analysis data
const costRanking = Object.entries(response.by_provider)
  .map(([provider, data]) => ({
    provider: provider,
    cost_per_request: data.cost_per_request,
    requests: data.requests
  }))
  .sort((a, b) => a.cost_per_request - b.cost_per_request);
```

**Grafana Panel Type:** `Bar Chart` (horizontal)
**Fields to Use:** Provider → Cost per Request
**Sort:** Ascending (lowest cost first)

---

### Panel 5: Latency Distribution (Box Plot)
**Source Endpoint:**
```
GET /api/monitoring/latency-trends/{provider}?hours=24
```

**Path Parameters:**
```
{provider} = "openrouter" (or other)
```

**Response Schema:**
```json
{
  "provider": "string",
  "timestamp": "ISO-8601",
  "models": [
    {
      "model": "string",               // e.g., "gpt-4o"
      "latencies": {
        "p50": "integer",              // e.g., 278
        "p95": "integer",              // e.g., 567
        "p99": "integer",              // e.g., 892
        "min": "integer",              // e.g., 145
        "max": "integer",              // e.g., 2345
        "avg": "number"                // e.g., 342.5
      }
    },
    ...
  ]
}
```

**Alternative Endpoint (Direct Percentiles):**
```
GET /api/monitoring/latency/{provider}/{model}?percentiles=50,95,99
```

**Alternative Response Schema:**
```json
{
  "provider": "string",
  "model": "string",
  "count": "integer",
  "avg": "number",
  "p50": "integer",
  "p95": "integer",
  "p99": "integer",
  "min": "integer",
  "max": "integer",
  "stddev": "number"
}
```

**Data Transformation:**
```javascript
// Format for box plot showing p50 (box), p95/p99 (whiskers)
const latencyBoxes = models.map(m => ({
  model: m.model,
  min: m.latencies.min,
  q1: m.latencies.p50,    // simplified: use p50 as median
  median: m.latencies.p95,
  q3: m.latencies.p99,
  max: m.latencies.max
}))
```

**Grafana Panel Type:** `Box Plot` (or Table with conditional formatting)
**Fields to Use:** p50, p95, p99, min, max

---

### Panel 6: Success Rate vs Usage (Scatter)
**Source Endpoints (combine two):**
```
1. GET /v1/models/trending?limit=10
2. GET /api/monitoring/error-rates?hours=24
```

**Data Transformation:**
```javascript
// Combine trending data with error rates
const scatter = trendingData.map(model => {
  const errors = errorData[model.provider]?.[model.model];
  return {
    x: model.requests,           // Usage (X-axis)
    y: 1 - (errors?.error_rate || 0),  // Success rate (Y-axis)
    size: model.total_cost,       // Bubble size = cost
    color: model.provider,        // Color = provider
    label: model.model
  };
});
```

**Grafana Panel Type:** `Scatter Plot`
**X-Axis:** Request count
**Y-Axis:** Success rate (%)
**Size:** Cost
**Color:** Provider

---

### Panel 7: Model Performance Heatmap (24h)
**Source Endpoint:**
```
GET /api/monitoring/latency-trends/{provider}?hours=24
```

**Response Schema:** (see Panel 5)

**Data Transformation:**
```javascript
// Create time series for each model's latency
// With hourly granularity (if available)
const heatmapData = models.map(m => ({
  seriesName: m.model,
  datapoints: hourlyData[m.model]?.map(d => [d.timestamp, d.avg_latency])
}))
```

**Grafana Panel Type:** `Heat Map`
**X-Axis:** Time (1h granularity)
**Y-Axis:** Model names
**Color:** Latency (green = fast, red = slow)

---

### Panel 8: Model Health Score (Gauge)
**Source Endpoints (combine):**
```
1. GET /v1/models/trending?limit=3  (top 3 models)
2. GET /api/monitoring/error-rates (for error rate)
3. GET /api/monitoring/latency/{provider}/{model} (for latency)
```

**Data Transformation:**
```javascript
// Calculate composite health score for top 3 models
// Formula: (reliability_score * 0.5) + (speed_score * 0.3) + (popularity_score * 0.2)
const topModels = trending.slice(0, 3);
const healthScores = topModels.map(m => {
  const errorRate = getErrorRate(m.provider, m.model);
  const latency = getLatency(m.provider, m.model);
  const reliability = (1 - errorRate) * 100;
  const speed = Math.max(0, 100 - (latency / 5));  // normalized
  const popularity = Math.min(100, m.requests / maxRequests * 100);
  const health = (reliability * 0.5) + (speed * 0.3) + (popularity * 0.2);
  return { model: m.model, score: health };
});
const avgHealth = scores.reduce((sum, s) => sum + s.score, 0) / scores.length;
```

**Grafana Panel Type:** `Gauge` or `Big Number`
**Field to Use:** Composite health score (0-100)

---

## Dashboard 3: Gateway & Provider Comparison

### Panel 1: Provider Health Scorecard (Grid)
**Source Endpoint:**
```
GET /api/monitoring/health
```

**Response Schema:** (see Dashboard 1, Panel 1)

**Data Transformation:**
```javascript
// Map all 17 providers to health status
const scorecard = response.map(p => ({
  provider: p.provider,
  health_score: p.health_score,
  status: p.status
}))
```

**Grafana Panel Type:** `Gauge Grid` (6 columns, 3 rows)
**Fields to Use:** `provider`, `health_score`
**Gauge Range:** 0-100
**Thresholds:**
- 0-60: Red (Down)
- 60-80: Yellow (Degraded)
- 80-100: Green (Healthy)

---

### Panel 2: Provider Comparison Matrix (Table)
**Source Endpoints (combine multiple):**
```
1. GET /api/monitoring/health
2. GET /api/monitoring/stats/realtime?hours=24
3. GET /api/monitoring/error-rates?hours=24
4. GET /api/monitoring/cost-analysis?days=1
```

**Data Transformation:**
```javascript
// Build comprehensive table for all providers
const matrix = providers.map(p => ({
  provider: p.provider,
  health_score: p.health_score,
  requests: statsData.providers[p.provider]?.total_requests || 0,
  error_rate: (aggregate error rate for provider * 100).toFixed(2) + '%',
  avg_latency: statsData.providers[p.provider]?.avg_latency_ms || 0,
  daily_cost: costData.by_provider[p.provider]?.total_cost || 0,
  uptime: '99.9%'  // Could come from separate endpoint
}))
```

**Grafana Panel Type:** `Table` (multi-column sortable)
**Columns:** Provider, Health Score, Requests/day, Error Rate %, Avg Latency (ms), Daily Cost, Uptime %

---

### Panel 3: Cost vs Reliability (Bubble Chart)
**Source Endpoints (combine):**
```
1. GET /api/monitoring/health
2. GET /api/monitoring/stats/realtime?hours=24
3. GET /api/monitoring/cost-analysis?days=1
```

**Data Transformation:**
```javascript
// Create bubble for each provider
const bubbles = providers.map(p => ({
  x: costData.by_provider[p.provider]?.cost_per_request,  // X: Cost
  y: p.health_score,                                        // Y: Reliability
  size: statsData.providers[p.provider]?.total_requests,   // Size: Volume
  color: p.provider,                                        // Color: Provider
  label: p.provider
}))
```

**Grafana Panel Type:** `Scatter Plot`
**X-Axis:** Cost per Request (dollars)
**Y-Axis:** Health Score (0-100)
**Bubble Size:** Request volume
**Bubble Color:** Consistent provider color

---

### Panel 4: Request Distribution (Donut)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Sum requests by provider
const distribution = Object.entries(response.providers).map(([provider, data]) => ({
  name: provider,
  value: data.total_requests
}))
```

**Grafana Panel Type:** `Pie Chart` (Donut style)
**Fields to Use:** Provider → Request count
**Legend:** Show percentages

---

### Panel 5: Cost Distribution (Pie)
**Source Endpoint:**
```
GET /api/monitoring/cost-analysis?days=1
```

**Response Schema:** (see Dashboard 2, Panel 4)

**Data Transformation:**
```javascript
// Use by_provider cost data
const costDist = Object.entries(response.by_provider).map(([provider, data]) => ({
  name: provider,
  value: data.total_cost
}))
```

**Grafana Panel Type:** `Pie Chart`
**Fields to Use:** Provider → Daily cost
**Legend:** Show percentages and dollar amounts

---

### Panel 6: Latency Distribution (Violin Plot)
**Source Endpoint:**
```
GET /api/monitoring/latency-trends/{provider}?hours=24
```

**Response Schema:** (see Dashboard 2, Panel 5)

**Data Transformation:**
```javascript
// Aggregate latency distribution for each provider across all models
const violins = providers.map(p => ({
  provider: p.provider,
  latencies: getAllLatenciesForProvider(p.provider)  // array of values
}))
```

**Grafana Panel Type:** `Violin Plot` (or Box Plot as alternative)
**Fields to Use:** All latency values per provider
**X-Axis:** Provider names
**Y-Axis:** Latency (ms)

---

### Panel 7: Cost Trend (Time Series)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=168
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Extract hourly cost from each provider's breakdown
const costTrend = [];
for (const [hour, hourData] of Object.entries(hourlyBreakdown)) {
  const providers = Object.entries(hourData.providers)
    .map(([name, data]) => ({ provider: name, cost: data.cost }));
  costTrend.push({ timestamp: hour, providers: providers });
}
```

**Grafana Panel Type:** `Time Series` (Stacked area)
**Fields to Use:** Provider cost per hour
**Stack:** Yes (stacked by provider)
**Time Range:** 7 days

---

### Panel 8: Uptime Trend (Time Series)
**Source Endpoint:**
```
GET /api/monitoring/health (called periodically and stored)
```

**Alternative (if available):**
```
GET /api/monitoring/providers/availability?days=7
```

**Data Transformation:**
```javascript
// For each hour in 7 days, calculate availability %
// Based on health_score >= 80 = available
const uptimeTrend = hours.map(h => ({
  timestamp: h,
  providers: providers.map(p => ({
    provider: p.provider,
    uptime: (healthHistoryForHour[p.provider] >= 80 ? 100 : 0)
  }))
}))
```

**Grafana Panel Type:** `Time Series` (Line)
**Fields to Use:** Provider uptime percentage
**Range:** 0-100%
**Y-Axis Label:** Uptime %

---

## Dashboard 4: Business & Financial Metrics

### Panel 1: Daily Revenue (Big Stat)
**Source Endpoint:**
```
GET /v1/models/trending?time_range=24h
```

**Note:** Assuming trending data includes revenue (or this comes from a business metrics endpoint)

**Data Transformation:**
```javascript
// Sum all revenue from today's requests
// This would need a dedicated endpoint or calculation
const dailyRevenue = trending.reduce((sum, m) => sum + calculateRevenue(m), 0)
```

**Alternative:**
```
GET /api/monitoring/business-metrics?period=day
```

**Grafana Panel Type:** `Stat` (Big Number with currency)
**Unit:** `currencyUSD`
**Format:** `$X,XXX.XX`

---

### Panel 2: Daily Cost (Big Stat)
**Source Endpoint:**
```
GET /api/monitoring/cost-analysis?days=1
```

**Response Schema:** (see Dashboard 2, Panel 4)

**Data Transformation:**
```javascript
const dailyCost = response.total_cost
```

**Grafana Panel Type:** `Stat` (Big Number with currency)
**Unit:** `currencyUSD`
**Field to Use:** `total_cost`

---

### Panel 3: Profit Margin (Big Stat with percentage)
**Source Endpoints:**
```
1. Daily Revenue (from Panel 1)
2. Daily Cost (from Panel 2)
```

**Data Transformation:**
```javascript
const margin = ((revenue - cost) / revenue * 100).toFixed(2)
```

**Grafana Panel Type:** `Stat` (Big Number)
**Unit:** `percent`
**Field to Use:** Calculated margin

---

### Panel 4: Cost Breakdown by Model (Treemap)
**Source Endpoint:**
```
GET /api/monitoring/cost-analysis?days=30
```

**Response Schema:** (see Dashboard 2, Panel 4)

**Data Transformation:**
```javascript
// Build hierarchical structure: Provider > Model > Cost
const treemapData = [];
for (const [provider, data] of Object.entries(response.by_provider)) {
  // Get model costs for this provider
  const models = getModelsForProvider(provider);
  models.forEach(m => {
    treemapData.push({
      value: m.total_cost,
      label: m.model,
      parent: provider,
      color: m.total_cost  // size determines color
    });
  });
}
```

**Grafana Panel Type:** `Treemap`
**Fields to Use:** Provider → Model → Cost
**Color:** Cost amount (gradient from green to red)
**Size:** Cost value

---

### Panel 5: Cost Trend with Budget (Area Chart)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=168
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Daily cost from hourly breakdown
const costTrend = [];
let dailySum = 0;
for (const [hour, hourData] of Object.entries(hourlyBreakdown)) {
  const hourCost = sum all provider costs for this hour;
  dailySum += hourCost;
  if (isEndOfDay(hour)) {
    costTrend.push({ date: hour.split('T')[0], cost: dailySum });
    dailySum = 0;
  }
}
```

**Grafana Panel Type:** `Time Series` (Area chart)
**Fields to Use:** Daily cost (line), 7d rolling avg (line), Budget threshold (dashed line)
**Fill:** Yes (area under curve)

---

### Panel 6: Cost vs Request Volume (Dual Axis)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=168
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Hourly data with both cost and request count
const dualAxis = hourlyData.map(h => ({
  timestamp: h.hour,
  requests: h.total_requests,
  cost: h.total_cost,
  costPerRequest: h.total_cost / h.total_requests
}))
```

**Grafana Panel Type:** `Time Series` (Dual Axis)
**Left Y-Axis:** Request count (bar chart)
**Right Y-Axis:** Cost (line chart)
**Legend:** Show both metrics

---

### Panel 7: Top 5 Expensive Models (Bar Chart)
**Source Endpoint:**
```
GET /api/monitoring/cost-analysis?days=30
```

**Response Schema:** (see Dashboard 2, Panel 4)

**Data Transformation:**
```javascript
// Sort models by cost, take top 5
const expensiveModels = response.data
  .sort((a, b) => b.total_cost - a.total_cost)
  .slice(0, 5)
  .map(m => ({
    model: m.model,
    cost: m.total_cost,
    requests: m.requests
  }))
```

**Grafana Panel Type:** `Bar Chart` (horizontal)
**Fields to Use:** Model → Cost
**Sort:** Descending (highest cost first)

---

### Panel 8: Cost Optimization Tips (Text)
**Source:** Static or AI-generated recommendations

**Display Format:** Markdown

**Content Example:**
```markdown
## Cost Optimization Recommendations

- **Switch 15% of GPT-4 requests to Claude-3-Haiku**
  Potential savings: $456/month

- **Enable response caching for common queries**
  Estimated reduction: 8-12%

- **Migrate batch processing to cheaper time windows**
  Potential savings: $234/month

- **Use Claude-3-Sonnet for non-critical requests**
  Potential savings: $123/month
```

**Grafana Panel Type:** `Text Panel`

---

## Dashboard 5: Incident Response

### Panel 1: Active Alerts & Anomalies (Alert List)
**Source Endpoint:**
```
GET /api/monitoring/anomalies
```

**Response Schema:** (see Dashboard 1, Panel 8)

**Data Transformation:**
```javascript
// Sort by severity, then by time (newest first)
const alerts = response.anomalies
  .sort((a, b) => {
    const severityMap = { critical: 0, warning: 1, info: 2 };
    const severityDiff = severityMap[a.severity] - severityMap[b.severity];
    return severityDiff !== 0 ? severityDiff : new Date(b.hour) - new Date(a.hour);
  })
```

**Grafana Panel Type:** `Alert List`
**Fields to Use:** `type`, `provider`, `severity`, `hour`, `value` vs `expected`
**Auto-refresh:** 15 seconds

---

### Panel 2: Error Rate (Real-time, with thresholds)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=1
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Calculate error rate from each provider
const errorRates = Object.entries(response.providers).map(([provider, data]) => ({
  provider: provider,
  error_rate: (data.error_rate * 100).toFixed(2),
  requests: data.total_requests
}))
```

**Grafana Panel Type:** `Time Series` (with threshold bands)
**Fields to Use:** Error rate per provider
**Thresholds:**
- 5% (Info) → Blue zone
- 10% (Warning) → Orange zone
- 25% (Critical) → Red zone
**Auto-refresh:** 10 seconds

---

### Panel 3: SLO Compliance Gauge
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Calculate % of requests that met SLO (latency < 500ms, no error)
const sloTarget = 99;  // 99% SLO
const metricsData = response.providers;
const successfulRequests = metricsData
  .filter(p => p.avg_latency_ms < 500 && p.error_rate < 0.01)
  .reduce((sum, p) => sum + p.total_requests, 0);
const totalRequests = metricsData.reduce((sum, p) => sum + p.total_requests, 0);
const sloCompliance = (successfulRequests / totalRequests * 100).toFixed(1);
```

**Grafana Panel Type:** `Gauge`
**Range:** 0-100%
**Target:** 99%
**Thresholds:**
- 95-100: Green (Met)
- 90-95: Orange (Warning)
- 0-90: Red (Failed)

---

### Panel 4: Recent Errors (Table - Tail)
**Source Endpoint:**
```
GET /api/monitoring/errors/{provider}?limit=100
```

**Query Parameters:**
```
{provider} = specific provider OR use endpoint that returns all
limit=100
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "provider": "string",
  "errors": [
    {
      "time": "ISO-8601",              // e.g., "2025-12-28T23:45:12Z"
      "model": "string",               // e.g., "llama-70b"
      "error_type": "string",          // "Timeout" | "Rate Limit" | "OOM" | etc.
      "error_message": "string",       // Full error text
      "count": "integer",              // How many of this error type
      "status_code": "integer"         // HTTP status or error code
    },
    ...
  ]
}
```

**Data Transformation:**
```javascript
// Get errors from all providers, merge, sort by time (newest first)
const recentErrors = [];
for (const provider of allProviders) {
  const providerErrors = await getErrors(provider);
  recentErrors.push(...providerErrors.errors);
}
recentErrors.sort((a, b) => new Date(b.time) - new Date(a.time));
```

**Grafana Panel Type:** `Table` (with tail/scrolling)
**Columns:** Time, Model, Error Type, Error Message, Count, Provider
**Row Limit:** 20-50 (most recent)
**Auto-refresh:** 5 seconds

---

### Panel 5: Circuit Breaker Status (Grid)
**Source Endpoint:**
```
GET /api/monitoring/circuit-breakers
```

**Response Schema:**
```json
{
  "timestamp": "ISO-8601",
  "circuit_breakers": {
    "openrouter": {
      "state": "string",               // "CLOSED" | "OPEN" | "HALF_OPEN"
      "failure_count": "integer",
      "success_count": "integer",
      "last_transition": "ISO-8601"
    },
    ...
  }
}
```

**Data Transformation:**
```javascript
// Map each provider's circuit breaker state
const cbStatus = Object.entries(response.circuit_breakers).map(([provider, cb]) => ({
  provider: provider,
  state: cb.state,
  failures: cb.failure_count,
  color: cb.state === 'CLOSED' ? 'green' : (cb.state === 'HALF_OPEN' ? 'yellow' : 'red')
}))
```

**Grafana Panel Type:** `Status Indicator Grid` (4 columns)
**Fields to Use:** Provider, State
**Status Mapping:**
- CLOSED → Green ("OK")
- HALF_OPEN → Yellow ("Testing")
- OPEN → Red ("Tripped")

---

### Panel 6: Provider Availability Heatmap (24h)
**Source Endpoint:**
```
GET /api/monitoring/health (called hourly and stored)
```

**Alternative:**
```
GET /api/monitoring/providers/availability?days=1
```

**Data Transformation:**
```javascript
// Create matrix: 24 hours (X) x 17 providers (Y)
// Value: health_score at that hour
const heatmapData = hours.map(h => {
  return providers.map(p => ({
    hour: h,
    provider: p.provider,
    value: getHealthScoreForHourProvider(h, p.provider)
  }));
});
```

**Grafana Panel Type:** `Heat Map`
**X-Axis:** Hour (0-23)
**Y-Axis:** Provider names
**Color:** Green (healthy >80) → Yellow (degraded 60-80) → Red (down <60)

---

### Panel 7: Request Success Rate (Time Series)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Data Transformation:**
```javascript
// Calculate success rate = 1 - error_rate for each provider
const successRates = Object.entries(response.providers.hourly_breakdown)
  .map(([hour, data]) => ({
    timestamp: hour,
    successRate: (1 - data.error_rate) * 100
  }))
```

**Grafana Panel Type:** `Time Series` (Line)
**Fields to Use:** Success rate per hour
**Y-Axis:** 0-100%
**Target line:** 99% SLA
**Auto-refresh:** 15 seconds

---

### Panel 8: Application Logs (Logs Panel)
**Source Endpoint:**
```
GET /api/logs?limit=100&level=error
```

**Data Format:** Text logs or structured JSON

**Grafana Panel Type:** `Logs Panel`
**Auto-refresh:** 5 seconds
**Highlight:** Error lines in red, warnings in yellow

---

## Dashboard 6: Tokens & Throughput Analysis

### Panel 1: Total Tokens (24h)
**Source Endpoint:**
```
GET /api/monitoring/stats/realtime?hours=24
```

**Response Schema:** (see Dashboard 1, Panel 2)

**Note:** The response includes token data if available

**Alternative Endpoint:**
```
GET /v1/chat/completions/metrics/tokens-per-second?time=hour
```

**Data Transformation:**
```javascript
// Sum all input + output tokens from 24 hours
const totalTokens = Object.values(response.providers)
  .reduce((sum, p) => sum + (p.total_tokens || 0), 0)
```

**Grafana Panel Type:** `Stat` (Big Number)
**Unit:** `short` (show as 2.34B, etc.)
**Format:** `0.00a` (e.g., 2.34B)

---

### Panel 2: Tokens per Second
**Source Endpoint:**
```
GET /v1/chat/completions/metrics/tokens-per-second?time=hour
```

**Response Schema (Prometheus format):**
```
# HELP gatewayz_tokens_per_second Token throughput by model and provider
# TYPE gatewayz_tokens_per_second gauge
gatewayz_tokens_per_second{model="gpt-4",provider="openrouter",requests="1234"} 245.67
gatewayz_tokens_per_second{model="claude-3-opus",provider="anthropic"} 189.34
```

**Data Transformation:**
```javascript
// Parse Prometheus format and sum across all models
const tps = parsePrometheusMetric(response)
  .reduce((sum, m) => sum + m.value, 0)
```

**Grafana Panel Type:** `Stat`
**Unit:** `short`
**Field:** Tokens/second

---

### Panel 3: Cost per Million Tokens
**Source Endpoints:**
```
1. GET /api/monitoring/stats/realtime?hours=24 (for cost)
2. Tokens from above (for token count)
```

**Data Transformation:**
```javascript
const dailyCost = response.total_cost;
const dailyTokens = totalTokensFromPanel1;
const costPer1MTokens = (dailyCost / (dailyTokens / 1_000_000)).toFixed(2);
```

**Grafana Panel Type:** `Stat`
**Unit:** `currencyUSD`
**Format:** `$X.XX` per 1M tokens

---

### Panel 4: Tokens by Model (Stacked Bar)
**Source Endpoint:**
```
GET /v1/models/trending?limit=15&sort_by=total_tokens
```

**Response Schema:** (see Dashboard 2, Panel 1)

**Data Transformation:**
```javascript
// Bar chart with input/output split
const tokensByModel = response.data
  .map(m => ({
    model: m.model,
    input_tokens: calculateInputTokens(m),
    output_tokens: m.total_tokens - calculateInputTokens(m)
  }))
```

**Grafana Panel Type:** `Bar Chart` (horizontal, stacked)
**Stack:** Input/Output split (different colors)
**Fields to Use:** Model name → Input tokens (blue) → Output tokens (orange)

---

### Panel 5: Input:Output Ratio (Scatter)
**Source Endpoint:**
```
GET /v1/models/trending?limit=20
```

**Response Schema:** (see Dashboard 2, Panel 1)

**Data Transformation:**
```javascript
// Calculate I:O ratio for each model
const ioRatio = response.data.map(m => ({
  model: m.model,
  input_ratio: m.input_tokens / m.total_tokens,
  output_ratio: m.output_tokens / m.total_tokens,
  cost: m.total_cost,
  provider: m.provider
}))
```

**Grafana Panel Type:** `Scatter Plot`
**X-Axis:** Input token ratio (0-1)
**Y-Axis:** Output token ratio (0-1)
**Bubble Size:** Cost
**Color:** Provider

---

### Panel 6: Throughput Ranking (Bar Chart)
**Source Endpoint:**
```
GET /v1/chat/completions/metrics/tokens-per-second?time=week
```

**Response Schema:** (Prometheus format - see Panel 2)

**Data Transformation:**
```javascript
// Top 10 models by tokens/second
const rankings = parsePrometheusMetric(response)
  .sort((a, b) => b.value - a.value)
  .slice(0, 10)
  .map((m, idx) => ({
    rank: idx + 1,
    model: m.labels.model,
    tps: m.value,
    trend: calculateTrend(m)
  }))
```

**Grafana Panel Type:** `Table` or `Bar Chart`
**Fields to Use:** Model → Tokens/second
**Sort:** Descending by throughput
**Include:** Trend sparkline

---

### Panel 7: Tokens/Sec Trend (Area Chart)
**Source Endpoint:**
```
GET /v1/chat/completions/metrics/tokens-per-second?time=week
```

**Response Schema:** (see Panel 2)

**Data Transformation:**
```javascript
// Aggregate hourly TPS from 7-day window
const tpsTrend = [];
for (const hour of last7Days) {
  const hourData = getTokensPerSecondForHour(hour);
  tpsTrend.push({
    timestamp: hour,
    tps: hourData.reduce((sum, m) => sum + m.value, 0)
  });
}
```

**Grafana Panel Type:** `Time Series` (Stacked area by provider)
**Fields to Use:** Tokens/second per hour
**Stack:** By provider (different colors)
**Granularity:** 1 hour

---

### Panel 8: Cost per Token Trend (Line Chart)
**Source Endpoints:**
```
1. GET /api/monitoring/stats/realtime?hours=168 (for cost)
2. Token data from above (for tokens)
```

**Data Transformation:**
```javascript
// Calculate hourly cost/token
const costPerTokenTrend = hourlyData.map(h => ({
  timestamp: h.hour,
  costPerToken: h.total_cost / h.total_tokens,
  benchmark: 0.000015  // Industry benchmark
}))
```

**Grafana Panel Type:** `Time Series` (Line with reference line)
**Fields to Use:** Cost/token (line), benchmark (dashed line)
**Y-Axis:** Cost per token (e.g., $0.000025)
**Granularity:** 1 hour, 7 days

---

## Summary: All Endpoints Used

| Endpoint | Dashboards | Purpose |
|----------|-----------|---------|
| GET /api/monitoring/health | 1, 3, 5 | Provider health status |
| GET /api/monitoring/stats/realtime | 1, 2, 4, 5, 6 | Real-time metrics (requests, cost, tokens) |
| GET /api/monitoring/error-rates | 1, 2 | Error rate by provider/model |
| GET /api/monitoring/anomalies | 1, 5 | Anomaly detection alerts |
| GET /api/monitoring/cost-analysis | 2, 4 | Cost breakdown by provider/model |
| GET /api/monitoring/latency-trends/{provider} | 2, 3 | Latency trends over time |
| GET /api/monitoring/latency/{provider}/{model} | 2 | Latency percentiles |
| GET /api/monitoring/errors/{provider} | 5 | Error logs |
| GET /api/monitoring/circuit-breakers | 5 | Circuit breaker status |
| GET /v1/models/trending | 2, 4, 6 | Top trending models |
| GET /v1/models/low-latency | 1 | Fast models |
| GET /v1/provider | 3 | Provider list |
| GET /v1/gateways/summary | 1 | Gateway statistics |
| GET /v1/models | 2 | Model catalog |
| GET /v1/chat/completions/metrics/tokens-per-second | 6 | Token throughput metrics |

---

**Status:** Ready for Grafana dashboard implementation
**Last Updated:** 2025-12-28
**Total Dashboards:** 6
**Total Panels:** 48
**Total Endpoints Used:** 15+
