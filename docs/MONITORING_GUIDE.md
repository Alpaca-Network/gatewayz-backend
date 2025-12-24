# Monitoring Guide: Unified Chat Endpoint

This guide covers monitoring, alerting, and observability for the unified chat endpoint.

## Table of Contents

- [Key Metrics](#key-metrics)
- [Dashboards](#dashboards)
- [Alerts](#alerts)
- [Logs](#logs)
- [Tracing](#tracing)
- [User Analytics](#user-analytics)
- [Troubleshooting](#troubleshooting)

---

## Key Metrics

### Request Metrics

#### Total Requests
```promql
# Total requests to unified endpoint
sum(rate(http_requests_total{path="/v1/chat"}[5m]))

# Total requests to legacy endpoints
sum(rate(http_requests_total{path=~"/v1/chat/completions|/v1/messages|/v1/responses"}[5m]))
```

**What to watch:**
- Gradual increase in `/v1/chat` requests
- Gradual decrease in legacy endpoint requests
- Combined total should remain stable

#### Request Rate by Format
```promql
# Requests by detected format
sum(rate(http_requests_total{path="/v1/chat"}[5m])) by (request_format)
```

**Expected distribution:**
- `openai`: 60-70% (most common)
- `anthropic`: 20-30%
- `responses`: 5-10%

### Performance Metrics

#### Response Time
```promql
# P50, P95, P99 latency for unified endpoint
histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (le))
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (le))
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (le))
```

**Targets:**
- P50: < 500ms
- P95: < 2s
- P99: < 5s

#### Response Time by Provider
```promql
# Latency by provider
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (provider, le))
```

**What to watch:**
- Consistent performance across providers
- Identify slow providers
- Track provider failover patterns

### Error Metrics

#### Error Rate
```promql
# Overall error rate
sum(rate(http_requests_total{path="/v1/chat",status=~"5.."}[5m]))
/
sum(rate(http_requests_total{path="/v1/chat"}[5m]))
```

**Target:** < 1%

#### Error Rate by Status Code
```promql
# Errors by status code
sum(rate(http_requests_total{path="/v1/chat"}[5m])) by (status)
```

**What to watch:**
- `400`: Client errors (should be low)
- `401`: Auth failures (track for security)
- `402`: Insufficient credits (business metric)
- `422`: Invalid requests (track for API usability)
- `500`: Server errors (should be near zero)
- `503`: Provider unavailable (track provider health)

#### Error Rate by Provider
```promql
# Provider errors
sum(rate(provider_requests_total{status=~"error|failed"}[5m])) by (provider)
```

**What to watch:**
- High error rate from specific provider → Provider issue
- Failover working correctly

### Business Metrics

#### Format Detection Accuracy
```promql
# Format detection vs explicit format
sum(rate(format_detection_total[5m])) by (detected_format, explicit_format)
```

**What to watch:**
- Mismatches between detected and explicit format
- Users relying on auto-detection vs explicit format

#### Deprecation Header Impressions
```promql
# Users hitting legacy endpoints (deprecation headers shown)
sum(rate(deprecation_header_served_total[5m])) by (endpoint)
```

**What to watch:**
- Decreasing trend (users migrating)
- Identify users still on legacy endpoints

#### Provider Failover Rate
```promql
# Failover events
sum(rate(provider_failover_total[5m])) by (from_provider, to_provider)
```

**What to watch:**
- Frequent failovers → Provider instability
- Failover success rate

---

## Dashboards

### Dashboard 1: Unified Endpoint Overview

**Panels:**
1. **Request Rate** (Time series)
   - Unified endpoint requests
   - Legacy endpoint requests
   - Total requests

2. **Response Time** (Time series)
   - P50, P95, P99 latency
   - By endpoint (unified vs legacy)

3. **Error Rate** (Time series)
   - Overall error rate
   - By status code

4. **Format Distribution** (Pie chart)
   - OpenAI format %
   - Anthropic format %
   - Responses API format %

5. **Top Models** (Bar chart)
   - Most requested models
   - Request count

6. **Top Users** (Table)
   - User ID
   - Request count
   - Format used

### Dashboard 2: Migration Progress

**Panels:**
1. **Migration Funnel** (Gauge)
   - % of requests using unified endpoint
   - Target: 100% by June 2025

2. **Legacy Endpoint Usage** (Time series)
   - Requests per legacy endpoint
   - Trend line

3. **Deprecation Header Impressions** (Time series)
   - Count of deprecation headers served
   - Unique users seeing headers

4. **User Migration Status** (Table)
   - Users still on legacy endpoints
   - Request count
   - Last seen date

5. **Migration Timeline** (Gantt chart)
   - Key milestones
   - Sunset date countdown

### Dashboard 3: Performance Deep Dive

**Panels:**
1. **Latency Heatmap** (Heatmap)
   - Request latency distribution
   - By hour of day

2. **Provider Performance** (Time series)
   - Latency by provider
   - Success rate by provider

3. **Format Detection Time** (Time series)
   - Time spent in format detection
   - Should be < 5ms

4. **Response Formatting Time** (Time series)
   - Time spent formatting responses
   - Should be < 10ms

5. **Database Query Time** (Time series)
   - Auth queries
   - Credit check queries
   - History queries

### Dashboard 4: Error Analysis

**Panels:**
1. **Error Rate by Endpoint** (Time series)
   - Compare unified vs legacy

2. **Error Distribution** (Pie chart)
   - By status code

3. **Provider Errors** (Table)
   - Provider name
   - Error count
   - Error rate
   - Last error time

4. **Recent Errors** (Log panel)
   - Last 100 errors
   - With stack traces

5. **Error Patterns** (Time series)
   - Common error messages
   - Frequency

---

## Alerts

### Critical Alerts (Page on-call immediately)

#### High Error Rate
```yaml
alert: UnifiedEndpointHighErrorRate
expr: |
  sum(rate(http_requests_total{path="/v1/chat",status=~"5.."}[5m]))
  /
  sum(rate(http_requests_total{path="/v1/chat"}[5m]))
  > 0.05
for: 5m
severity: critical
message: "Unified endpoint error rate above 5% for 5 minutes"
```

#### High Latency
```yaml
alert: UnifiedEndpointHighLatency
expr: |
  histogram_quantile(0.95,
    sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (le)
  ) > 5
for: 10m
severity: critical
message: "P95 latency above 5 seconds for 10 minutes"
```

#### Zero Requests
```yaml
alert: UnifiedEndpointZeroRequests
expr: |
  sum(rate(http_requests_total{path="/v1/chat"}[5m])) == 0
for: 10m
severity: critical
message: "No requests to unified endpoint for 10 minutes - possible deployment issue"
```

### Warning Alerts (Notify team)

#### Elevated Error Rate
```yaml
alert: UnifiedEndpointElevatedErrors
expr: |
  sum(rate(http_requests_total{path="/v1/chat",status=~"5.."}[5m]))
  /
  sum(rate(http_requests_total{path="/v1/chat"}[5m]))
  > 0.01
for: 15m
severity: warning
message: "Unified endpoint error rate above 1% for 15 minutes"
```

#### Slow Response Time
```yaml
alert: UnifiedEndpointSlowResponses
expr: |
  histogram_quantile(0.95,
    sum(rate(http_request_duration_seconds_bucket{path="/v1/chat"}[5m])) by (le)
  ) > 2
for: 15m
severity: warning
message: "P95 latency above 2 seconds for 15 minutes"
```

#### High Provider Failover Rate
```yaml
alert: HighProviderFailoverRate
expr: |
  sum(rate(provider_failover_total[5m])) > 10
for: 10m
severity: warning
message: "High provider failover rate - check provider health"
```

### Info Alerts (Track trends)

#### Format Detection Anomaly
```yaml
alert: FormatDetectionAnomaly
expr: |
  abs(
    sum(rate(format_detection_total{detected_format="openai"}[1h]))
    -
    sum(rate(format_detection_total{detected_format="openai"}[1h] offset 24h))
  ) / sum(rate(format_detection_total[1h] offset 24h)) > 0.2
for: 1h
severity: info
message: "Format distribution changed by >20% compared to yesterday"
```

#### Migration Stalled
```yaml
alert: MigrationStalled
expr: |
  sum(rate(http_requests_total{path=~"/v1/chat/completions|/v1/messages"}[7d]))
  /
  sum(rate(http_requests_total{path=~"/v1/chat|/v1/chat/completions|/v1/messages"}[7d]))
  > 0.5
for: 7d
severity: info
message: "More than 50% of requests still using legacy endpoints - migration may be stalled"
```

---

## Logs

### Important Log Events

#### Format Detection
```python
logger.info(f"Detected request format: {detected_format}")
```

**What to log:**
- Detected format
- Explicit format (if provided)
- Request path
- User ID (if authenticated)

**Use case:** Verify format detection accuracy

#### Deprecation Header Served
```python
logger.info(f"Deprecated endpoint used: {path} by {user_ip}")
```

**What to log:**
- Endpoint path
- User IP or ID
- Timestamp
- User agent

**Use case:** Track migration progress, identify users needing help

#### Provider Failover
```python
logger.warning(f"Provider {provider} failed, failing over to {fallback_provider}")
```

**What to log:**
- Original provider
- Fallback provider
- Error from original provider
- Failover success/failure

**Use case:** Track provider reliability

#### Response Format Mismatch
```python
logger.warning(f"Detected format {detected} doesn't match explicit format {explicit}")
```

**What to log:**
- Detected format
- Explicit format
- Request body (sanitized)

**Use case:** Identify format detection issues

### Log Queries

#### Find users still on legacy endpoints
```
path:"/v1/chat/completions" OR path:"/v1/messages"
| stats count by user_id
| sort by count desc
```

#### Identify format detection errors
```
"Format detection" AND "error"
| stats count by error_message
```

#### Track provider failures
```
"Provider failed" OR "failover"
| stats count by provider
| sort by count desc
```

---

## Tracing

### Distributed Traces

Use the trace ID from `X-Trace-ID` header to follow requests through the system:

#### Key Spans to Track

1. **Request Handler** (`unified_chat_endpoint`)
   - Duration: < 2s
   - Child spans: validation, processing, formatting

2. **Format Detection** (`detect_request_format`)
   - Duration: < 5ms
   - Tracks: which format was detected

3. **Chat Processing** (`chat_handler.process_chat`)
   - Duration: varies (depends on provider)
   - Child spans: provider selection, request, response

4. **Provider Request** (`provider.chat`)
   - Duration: varies by provider
   - Tracks: which provider was used

5. **Response Formatting** (`ResponseFormatter.format_response`)
   - Duration: < 10ms
   - Tracks: output format

#### Trace Analysis

**High latency traces:**
```
duration > 5s AND service:"unified-chat"
```

**Failed requests:**
```
error:true AND service:"unified-chat"
```

**Provider failover traces:**
```
span.name:"provider_failover"
```

---

## User Analytics

### Track Migration Progress

#### Migration Funnel
1. **Total users**: All users with API keys
2. **Active users**: Made request in last 30 days
3. **Users on legacy**: Used legacy endpoint in last 7 days
4. **Users migrated**: Only used `/v1/chat` in last 30 days

**Calculation:**
```sql
SELECT
  COUNT(DISTINCT user_id) as total_users,
  COUNT(DISTINCT CASE WHEN last_request > NOW() - INTERVAL '30 days' THEN user_id END) as active_users,
  COUNT(DISTINCT CASE WHEN last_legacy_request > NOW() - INTERVAL '7 days' THEN user_id END) as on_legacy,
  COUNT(DISTINCT CASE WHEN last_unified_request > NOW() - INTERVAL '30 days'
                       AND last_legacy_request < NOW() - INTERVAL '30 days'
                       THEN user_id END) as migrated
FROM user_analytics
```

#### Migration Rate
```
Migrated users / Active users * 100
```

**Target:** > 95% by May 2025

### High-Value User Tracking

Identify high-volume users still on legacy endpoints:

```sql
SELECT
  user_id,
  email,
  SUM(request_count) as total_requests,
  MAX(last_legacy_request) as last_legacy_use
FROM user_requests
WHERE endpoint IN ('/v1/chat/completions', '/v1/messages', '/v1/responses')
  AND timestamp > NOW() - INTERVAL '7 days'
GROUP BY user_id, email
HAVING SUM(request_count) > 1000
ORDER BY total_requests DESC
```

**Action:** Reach out proactively to help migrate

---

## Troubleshooting

### Problem: High error rate on unified endpoint

**Check:**
1. Error logs for common error messages
2. Which providers are failing
3. Format detection issues
4. Database connection issues

**Commands:**
```bash
# Check error distribution
grep "unified_chat_endpoint error" /var/log/app.log | grep -oP "status_code=\d+" | sort | uniq -c

# Check provider errors
grep "Provider.*failed" /var/log/app.log | tail -100

# Check database errors
grep "database.*error" /var/log/app.log | tail -50
```

### Problem: Slow response times

**Check:**
1. Provider latency
2. Database query time
3. Format detection/formatting time
4. Network issues

**Commands:**
```bash
# Check slow requests
grep "duration_ms>" /var/log/app.log | grep "path=/v1/chat" | sort -t'>' -k2 -nr | head -20

# Check provider performance
grep "provider_latency_ms" /var/log/app.log | awk '{print $provider, $latency}' | sort -k2 -nr
```

### Problem: Format detection inaccurate

**Check:**
1. Logs for format detection
2. Explicit vs detected format mismatches
3. Request samples

**Commands:**
```bash
# Find format mismatches
grep "Format mismatch" /var/log/app.log

# Check format distribution
grep "Detected request format" /var/log/app.log | grep -oP "format=\w+" | sort | uniq -c
```

### Problem: Users not migrating

**Check:**
1. Deprecation header delivery
2. Email delivery
3. User engagement with docs
4. Support ticket volume

**Actions:**
1. Check email bounce rate
2. Verify deprecation headers in responses
3. Reach out to high-volume users directly
4. Offer migration assistance calls

---

## Dashboard URLs

- **Overview:** https://grafana.gatewayz.ai/d/unified-chat-overview
- **Migration:** https://grafana.gatewayz.ai/d/migration-progress
- **Performance:** https://grafana.gatewayz.ai/d/performance-deep-dive
- **Errors:** https://grafana.gatewayz.ai/d/error-analysis

## Runbook

For detailed troubleshooting procedures, see: `docs/RUNBOOK.md`

---

**Last Updated:** 2025-12-23
**Owner:** Backend Team
**On-call:** Check PagerDuty schedule
