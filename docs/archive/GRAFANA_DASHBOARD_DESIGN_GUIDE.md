# Grafana Dashboard Design Guide
**Created:** 2025-12-28
**Purpose:** Visual design strategy and chart recommendations for GatewayZ monitoring dashboards
**Status:** 🎨 Ready for Implementation

---

## 📊 Dashboard Visualization Strategy

This guide provides a comprehensive approach to visualizing GatewayZ metrics across multiple Grafana dashboards, focusing on visual hierarchy, interactivity, and actionable insights.

---

## Dashboard 1: Executive Overview (Real-Time Heartbeat)

### Purpose
High-level health snapshot for management/ops teams. 5-second glances showing if systems are healthy or need attention.

### Layout Structure
```
┌─────────────────────────────────────────────────────────────────┐
│                    GATEWAY HEALTH STATUS                        │
│  [OpenRouter] [Portkey] [Together] [Fireworks] [HuggingFace]   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  System Health: [████████░] 92%     Active Requests: 12.5K/min │
│  Avg Response: 245ms                Total Cost: $1,245.67/day   │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  Request Volume (24h)        │  Error Rate by Provider         │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐  │
│  │ Line chart showing       │ │  │ Small multiples showing   │  │
│  │ request spike pattern    │ │  │ error % per provider      │  │
│  │ with 1h granularity      │ │  │ with color coding         │  │
│  └──────────────────────────┘ │  └──────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Stat (Gauge Ring) | Overall Health Score | 30s | Single KPI showing system status |
| **2** | Stat (with sparkline) | Active Requests/min | 15s | Quick request rate overview |
| **3** | Stat | Avg Response Time | 30s | Performance at a glance |
| **4** | Stat | Daily Cost | 60s | Budget tracking |
| **5** | Status Indicator Grid | Provider Health (17 items) | 60s | Visual health per provider (green/yellow/red) |
| **6** | Time Series (Line) | Request Volume (24h) | 30s | Trend visualization with legend |
| **7** | Pie Chart | Error Rate Distribution | 60s | Show % of errors by provider |
| **8** | Alert List | Critical Anomalies | 30s | Real-time alert feed |

### Color Scheme
- **Healthy:** Green (#31863B)
- **Warning:** Yellow (#FF9830)
- **Critical:** Red (#E02620)
- **Info:** Blue (#0099CC)

---

## Dashboard 2: Model Performance Analytics

### Purpose
Deep dive into which models are performing well, which are problematic, and where to invest resources.

### Layout Structure
```
┌──────────────────────────────────────────────────────────────────┐
│                    MODEL PERFORMANCE ANALYTICS                    │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  🔝 Top Models This Week    │  ⚠️ Models With Issues            │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐   │
│  │ 1. gpt-4o (5,234 req)    │ │  │ • llama-70b (8.2% error) │   │
│  │ 2. claude-3 (3,456 req)  │ │  │ • mistral (562ms latency)│   │
│  │ 3. gemini-3 (2,891 req)  │ │  │ • together-7b (↑cost)    │   │
│  └──────────────────────────┘ │  └──────────────────────────┘   │
│                                                                   │
├──────────────────────────────────────────────────────────────────┤
│  Model Requests (7d Trend)   │  Cost per Model (normalized)     │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐   │
│  │ Stacked bar chart showing│ │  │ Horizontal bar showing   │   │
│  │ request volume per model │ │  │ cost efficiency ranking  │   │
│  │ stacked by gateway       │ │  │ with sparkline trends    │   │
│  └──────────────────────────┘ │  └──────────────────────────┘   │
│                                                                   │
├──────────────────────────────────────────────────────────────────┤
│  Model Latency Percentiles       │  Success Rate by Model       │
│  ┌────────────────────────────────┐ │  ┌──────────────────────┐  │
│  │ Box/whisker chart showing      │ │  │ Scatter plot showing │  │
│  │ p50, p95, p99 latency spread   │ │  │ success % vs usage   │  │
│  │ for top 10 models              │ │  │ (bubble size = cost) │  │
│  └────────────────────────────────┘ │  └──────────────────────┘  │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Table (sorted) | Top 5 Models by Requests | 60s | Quick reference ranked list |
| **2** | Table (sorted) | Models with Errors | 30s | Alert-style problem identification |
| **3** | Bar Chart (Stacked) | Request Volume by Model (7d) | 60s | Weekly trend with multi-gateway view |
| **4** | Bar Chart (Horizontal) | Cost per Request (Ranked) | 300s | ROI/efficiency ranking |
| **5** | Box Plot | Latency Distribution (top 10) | 60s | Spread and outliers visualization |
| **6** | Scatter Plot | Success Rate vs Usage | 60s | Correlation between reliability & popularity |
| **7** | Heat Map | Model Performance Over Time | 60s | Quick identification of degradation |
| **8** | Gauge | Weighted Model Health | 30s | Composite score reflecting top 3 models |

### Interactivity
- **Click on model name** → drill down to model-specific dashboard
- **Time range selector** → last 24h, 7d, 30d, custom
- **Filter by provider** → isolate specific gateway data

---

## Dashboard 3: Gateway & Provider Comparison

### Purpose
Compare performance across 17+ providers and identify which gateways are most reliable/cost-effective.

### Layout Structure
```
┌───────────────────────────────────────────────────────────────┐
│              GATEWAY & PROVIDER PERFORMANCE MATRIX              │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  Provider Scorecard (17 providers)                            │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ OpenRouter  ████████░ 92  │ Portkey      ███████░░ 78   │ │
│  │ Together    █████░░░░ 61  │ Featherless  ██████░░░ 67   │ │
│  │ Fireworks   █████░░░░ 62  │ HuggingFace  █████░░░░ 59   │ │
│  │ ... 11 more providers                                    │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
├───────────────────────────────────────────────────────────────┤
│  Provider Comparison Matrix       │  Cost vs Reliability      │
│  ┌────────────────────────────────┐ │  ┌───────────────────┐  │
│  │ Table with key metrics:        │ │  │ Bubble chart:     │  │
│  │ Health, Requests, Cost, Error  │ │  │ X: Cost/req       │  │
│  │ Rate, Avg Latency, Uptime      │ │  │ Y: Success rate   │  │
│  │ Sortable & filterable          │ │  │ Size: Volume      │  │
│  └────────────────────────────────┘ │  └───────────────────┘  │
│                                                                │
├───────────────────────────────────────────────────────────────┤
│  Request Distribution (7d)        │  Latency Comparison      │
│  ┌────────────────────────────────┐ │  ┌───────────────────┐  │
│  │ Donut chart showing % volume   │ │  │ Violin plot       │  │
│  │ per provider with legend       │ │  │ showing latency   │  │
│  │ (hover for $$$)                │ │  │ distribution      │  │
│  └────────────────────────────────┘ │  └───────────────────┘  │
│                                                                │
└───────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Gauge Grid (6 cols) | Health Score per Provider | 60s | Quick status overview for all 17 |
| **2** | Table (multi-sort) | Provider Comparison Matrix | 300s | Comprehensive metrics table |
| **3** | Bubble Chart | Cost vs Reliability Scatter | 300s | Strategic positioning view |
| **4** | Donut/Pie Chart | Request Volume Distribution | 60s | Market share by provider |
| **5** | Pie Chart | Cost Distribution | 60s | Budget allocation across providers |
| **6** | Violin Plot | Latency Distribution | 60s | Statistical spread view |
| **7** | Time Series | Cost Trend per Provider | 300s | Budget forecasting |
| **8** | Time Series | Uptime % Trend | 300s | Reliability tracking |

### Design Considerations
- **17 providers = dense visualization**
  - Use small multiples rather than single large chart
  - Color code by provider for consistency across dashboards
  - Implement search/filter capability

- **Color Coding:** Assign consistent color to each provider across all dashboards
  - OpenRouter: #1f77b4 (Blue)
  - Portkey: #ff7f0e (Orange)
  - Together: #2ca02c (Green)
  - ... (16 more)

---

## Dashboard 4: Business & Financial Metrics

### Purpose
Track ROI, cost optimization opportunities, and revenue impact of model choices.

### Layout Structure
```
┌────────────────────────────────────────────────────────────┐
│            BUSINESS METRICS & COST OPTIMIZATION             │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  Revenue Today: $12,456  │ Cost Today: $3,245  │ Margin: 74% │
│  ↑ 8.2% vs yesterday    │ ↓ 2.1% vs yesterday │           │
│                                                             │
├────────────────────────────────────────────────────────────┤
│  Cost Breakdown by Model (30d)  │  Token Efficiency        │
│  ┌──────────────────────────────┐ │  ┌──────────────────┐  │
│  │ Treemap showing cost by      │ │  │ Scatter plot:    │  │
│  │ model with size = $$$ and    │ │  │ X: Cost/token    │  │
│  │ color = provider             │ │  │ Y: Throughput    │  │
│  │ (click to drill down)         │ │  │ Color: Provider  │  │
│  └──────────────────────────────┘ │  └──────────────────┘  │
│                                                             │
├────────────────────────────────────────────────────────────┤
│  Cost Trend (7d rolling avg)  │  Cost vs Requests Trade-off  │
│  ┌──────────────────────────────┐ │  ┌──────────────────┐   │
│  │ Area chart showing:           │ │  │ Dual axis chart: │   │
│  │ - Total daily cost (bar)      │ │  │ Bar: requests    │   │
│  │ - 7d rolling avg (line)       │ │  │ Line: avg cost   │   │
│  │ - Target budget line (dashed) │ │  │ Shows value/req  │   │
│  └──────────────────────────────┘ │  └──────────────────┘   │
│                                                             │
├────────────────────────────────────────────────────────────┤
│  Top Cost Models (7d)         │  Cost Optimization          │
│  ┌──────────────────────────────┐ │  ┌──────────────────┐   │
│  │ 1. gpt-4o: $2,456 (75%)     │ │  │ Recommendations: │   │
│  │ 2. claude-3: $567 (17%)     │ │  │ □ Switch 15% to  │   │
│  │ 3. gemini-3: $234 (8%)      │ │  │   cheaper alt    │   │
│  │                              │ │  │ □ Cache more     │   │
│  │ ⚠️ gpt-4o is 75% of budget   │ │  │   responses      │   │
│  └──────────────────────────────┘ │  └──────────────────┘   │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Stat (Big Number) | Daily Revenue | 60s | KPI tracking |
| **2** | Stat (Big Number) | Daily Cost | 60s | Expense tracking |
| **3** | Stat (with % change) | Profit Margin | 60s | Health indicator |
| **4** | Treemap | Cost by Model (30d) | 300s | Visual budget allocation |
| **5** | Area Chart | Cost Trend with Budget Line | 300s | Budget adherence tracking |
| **6** | Scatter Plot | Cost/Token vs Throughput | 300s | Efficiency matrix |
| **7** | Bar Chart | Top 5 Expensive Models | 300s | Quick cost focus |
| **8** | Text Panel | Cost Optimization Tips | Static | AI-generated recommendations |

### Data-Driven Insights
- **Cost vs Performance Curve**
  - Identify sweet spot between cost and response quality
  - Show which models are "over budget" for their performance

- **Trend Predictions**
  - Show 7-day and 30-day burn rate
  - Alert if trending over budget by 10%

---

## Dashboard 5: Real-Time Incident Response

### Purpose
For on-call engineers - quick identification of problems and drill-down capabilities.

### Layout Structure
```
┌────────────────────────────────────────────────────────────────┐
│                  INCIDENT RESPONSE DASHBOARD                    │
├────────────────────────────────────────────────────────────────┤
│  ALERTS & ANOMALIES (sorted by severity)                       │
│  🔴 CRITICAL: Together high error rate (32% > 25% threshold)  │
│  🟡 WARNING:  OpenRouter latency spike (1200ms > 600ms)       │
│  🟡 WARNING:  Daily cost $3,456 exceeds $3,200 budget        │
│  🔵 INFO:     Vercel gateway offline (degraded to 0 requests) │
│                                                                 │
├────────────────────────────────────────────────────────────────┤
│  Error Rate (Real-time)         │  Latency SLO Status         │
│  ┌──────────────────────────────┐ │  ┌──────────────────────┐ │
│  │ Time series with red zones   │ │  │ Gauge showing % of   │ │
│  │ showing alert thresholds     │ │  │ requests under SLO   │ │
│  │ Clickable to drill to errors │ │  │ (target: >99%)       │ │
│  └──────────────────────────────┘ │  └──────────────────────┘ │
│                                                                 │
├────────────────────────────────────────────────────────────────┤
│  Recent Errors (Table - tail)                                 │
│  ┌────────────────────────────────────────────────────────────┐│
│  │ Time        Model       Error            Count    Provider  ││
│  │ 23:45:12    llama-70b   Timeout          127     Together  ││
│  │ 23:42:58    gpt-4o      Rate Limit       45      OpenRout. ││
│  │ 23:39:21    claude-3    OOM Error        12      Portkey   ││
│  │ ... (auto-refreshing)                                       ││
│  └────────────────────────────────────────────────────────────┘│
│                                                                 │
├────────────────────────────────────────────────────────────────┤
│  Circuit Breaker Status     │  Provider Availability         │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐ │
│  │ Status grid showing if   │ │  │ Heat map: 24h x 17 prov  │ │
│  │ breakers are OPEN/CLOSED │ │  │ Red = down, Green = up   │ │
│  │ Color coded by severity  │ │  │ (easy to spot outages)   │ │
│  └──────────────────────────┘ │  └──────────────────────────┘ │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Alert List | Active Anomalies | 15s | Critical issues feed |
| **2** | Time Series | Error Rate (Real-time) | 10s | Trend with threshold bands |
| **3** | Gauge | SLO Compliance % | 30s | Target availability tracking |
| **4** | Table (Tail) | Recent Errors | 5s | Live error log with search |
| **5** | Status Panel Grid | Circuit Breaker States | 30s | Provider health indicators |
| **6** | Heat Map | Provider Availability (24h) | 60s | Outage pattern detection |
| **7** | Time Series | Request Success Rate | 15s | Live reliability view |
| **8** | Logs Panel | Application Logs | 5s | Raw debug data for investigation |

### UX Features
- **Auto-refresh every 5-10 seconds** (real-time incident view)
- **Color coding:** Red = Critical, Orange = Warning, Blue = Info
- **Click on error** → see full stack trace and affected requests
- **Click on provider** → drill to provider-specific dashboard
- **Top banner** → shows if any CRITICAL alerts exist

---

## Dashboard 6: Tokens & Throughput Analysis

### Purpose
Deep dive into token usage, efficiency, and throughput metrics for optimization.

### Layout Structure
```
┌────────────────────────────────────────────────────────────┐
│           TOKENS & THROUGHPUT ANALYSIS                      │
├────────────────────────────────────────────────────────────┤
│  Total Tokens (24h): 2.34B  │  Tokens/sec: 27K  │  Cost/1M: $2.45 │
│  ↑ 12% vs yesterday        │  ↑ 5.2% vs avg   │ ↓ 1.3% savings   │
│                                                                │
├────────────────────────────────────────────────────────────┤
│  Tokens per Model (24h)       │  Input:Output Ratio by Model │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐│
│  │ Horizontal bar chart     │ │  │ Scatter plot showing     ││
│  │ showing total tokens,    │ │  │ balance between input &  ││
│  │ split into input/output  │ │  │ output tokens per model  ││
│  │ stacked bars             │ │  │ (size = cost)            ││
│  └──────────────────────────┘ │  └──────────────────────────┘│
│                                                                │
├────────────────────────────────────────────────────────────┤
│  Token Efficiency Score       │  Throughput Ranking (tokens/sec) │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐ │
│  │ Gauge showing efficiency │ │  │ Top 10 models by        │ │
│  │ ratio: tokens used vs    │ │  │ throughput with trends  │ │
│  │ tokens estimated         │ │  │ (showing velocity)      │ │
│  └──────────────────────────┘ │  └──────────────────────────┘ │
│                                                                │
├────────────────────────────────────────────────────────────┤
│  Tokens/Sec Trend (7d)        │  Cost per Token Trend (7d)   │
│  ┌──────────────────────────┐ │  ┌──────────────────────────┐ │
│  │ Area chart showing       │ │  │ Line chart showing       │ │
│  │ tokens/sec with 1h gran. │ │  │ $/token with benchmark  │ │
│  │ stacked by provider      │ │  │ lines for comparison    │ │
│  └──────────────────────────┘ │  └──────────────────────────┘ │
│                                                                │
└────────────────────────────────────────────────────────────┘
```

### Recommended Charts

| Panel # | Type | Metric | Refresh | Purpose |
|---------|------|--------|---------|---------|
| **1** | Stat | Total Tokens (24h) | 60s | Volume KPI |
| **2** | Stat | Tokens per Second | 30s | Throughput KPI |
| **3** | Stat | Cost per Million Tokens | 300s | Efficiency KPI |
| **4** | Bar Chart (Horizontal, Stacked) | Tokens by Model (Input/Output) | 60s | Distribution view |
| **5** | Scatter Plot | Input:Output Ratio | 60s | Model characteristic matrix |
| **6** | Gauge | Efficiency vs Estimate | 60s | How well we predict |
| **7** | Time Series (Stacked) | Tokens/Sec by Provider | 60s | Throughput trend |
| **8** | Time Series | Cost/Token Trend | 300s | Unit economics trend |

---

## 🎨 Visual Design Principles

### Color Palettes

**Status Indicators:**
```
Healthy/Good:    #31863B (Green)      - Success, optimal
Warning:         #FF9830 (Orange)     - Attention needed
Critical:        #E02620 (Red)        - Immediate action
Info:            #0099CC (Blue)       - Informational
Neutral:         #808080 (Gray)       - Baseline/reference
```

**Provider Consistent Colors:**
```
OpenRouter:      #1f77b4 (Blue)
Portkey:         #ff7f0e (Orange)
Together:        #2ca02c (Green)
Fireworks:       #d62728 (Red)
HuggingFace:     #9467bd (Purple)
DeepInfra:       #8c564b (Brown)
...and 11 more
```

### Typography & Sizing

- **Titles:** 20-24px, Bold, title case
- **Subtitles:** 14-16px, Regular, sentence case
- **Values:** 28-48px (for big numbers), Bold
- **Labels:** 12-14px, Regular, lower case
- **Legends:** 12px, Regular

### Spacing & Layout

- **Dashboard margins:** 16px all sides
- **Panel margins:** 8px between panels
- **Panel padding:** 12px internal
- **Column width:** 12-column grid (standard Grafana)
- **Row height:** 250px per panel (responsive)

---

## 📊 Chart Type Selection Guide

| Metric Type | Best Chart | Reason | Alternative |
|-------------|-----------|--------|-------------|
| **Single KPI** | Gauge/Stat with unit | Clear, unambiguous | Value with sparkline |
| **Time Series** | Line/Area | Shows trends over time | Bar (for discrete periods) |
| **Comparison** | Bar/Column | Easy to compare values | Table (for many metrics) |
| **Composition** | Pie/Donut | Part-to-whole relationship | Treemap (for hierarchies) |
| **Distribution** | Histogram/Box plot | Shows spread | Violin (for symmetry view) |
| **Correlation** | Scatter plot | X-Y relationship | Heat map (for many pairs) |
| **Ranking** | Horizontal bar | Easy to read labels | Table (with sort) |
| **Real-time Feed** | Table/Logs | Up-to-date info | Alert list (for events) |

---

## 🔄 Recommended Refresh Rates

| Data Type | Refresh Interval | Reason |
|-----------|-----------------|--------|
| Health Status | 30-60s | Human perception threshold |
| Real-time Errors | 5-15s | Incident response needs |
| Latency/Performance | 30-60s | Short-term trend visibility |
| Cost/Budget | 60-300s | Less frequently changing |
| Historical Data (7d+) | 300-3600s | Change slowly, reduce load |
| Static Data (config) | None | Only on manual refresh |

---

## 🎯 Dashboard Access Recommendations

### For Different Personas

| Role | Primary Dashboard | Secondary | Use Case |
|------|------------------|-----------|----------|
| **Executive** | Executive Overview (Dash 1) | Business Metrics (Dash 4) | Budget & ROI tracking |
| **Ops Engineer** | Incident Response (Dash 5) | Gateway Comparison (Dash 3) | Issue resolution |
| **Product Manager** | Model Performance (Dash 2) | Business Metrics (Dash 4) | Feature planning |
| **Finance** | Business Metrics (Dash 4) | Executive Overview (Dash 1) | Cost control |
| **ML Engineer** | Tokens & Throughput (Dash 6) | Model Performance (Dash 2) | Optimization |

---

## 📱 Responsive Design Notes

- **Desktop (>1920px):** 2-3 columns per row
- **Laptop (1200-1920px):** 2 columns per row
- **Tablet (768-1200px):** 1 column per row
- **Mobile (<768px):** Stack all panels vertically

---

## ✅ Implementation Checklist

- [ ] Create datasources for each endpoint
  - [ ] JSON API datasource for `/api/monitoring/*`
  - [ ] JSON API datasource for `/v1/provider/*`, `/v1/gateway/*`, `/v1/models/*`
  - [ ] Prometheus datasource for `/metrics`

- [ ] Dashboard 1: Executive Overview (8 panels)
- [ ] Dashboard 2: Model Performance (8 panels)
- [ ] Dashboard 3: Gateway Comparison (8 panels)
- [ ] Dashboard 4: Business Metrics (8 panels)
- [ ] Dashboard 5: Incident Response (8 panels)
- [ ] Dashboard 6: Tokens & Throughput (8 panels)

- [ ] Configure alert rules for critical thresholds
- [ ] Set up dashboard variables for time range, provider filter
- [ ] Test with production data
- [ ] Document drill-down navigation paths

---

## 📞 Notes for Implementation

### Key Considerations

1. **Data Consistency:** Ensure all dashboards use same time range selector
2. **Drill-down Paths:** Each dashboard should have navigation to related dashboards
3. **Caching:** Use Grafana caching to reduce backend load for expensive queries
4. **Annotations:** Add deployment markers, incident times to time series
5. **Templating:** Use variables for provider, model selection across dashboards

### Performance Tips

- Use `limit` parameters in API calls to reduce payload
- Aggregate data server-side (use `/aggregated` endpoints if available)
- Cache expensive queries (daily cost, 30-day trends)
- Use downsampling for very large time ranges (30d+)

---

**Status:** Ready for Grafana implementation
**Last Updated:** 2025-12-28
**Authored For:** GatewayZ Backend Team
