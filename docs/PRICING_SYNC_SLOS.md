# Pricing Sync Scheduler - SLOs and SLIs

**Document**: Service Level Objectives (SLOs) and Service Level Indicators (SLIs)
**Component**: Automated Pricing Sync Scheduler
**Owner**: Platform Team
**Version**: 1.0
**Last Updated**: 2026-01-26

---

## Overview

This document defines the Service Level Objectives (SLOs) and Service Level Indicators (SLIs) for the automated pricing sync scheduler. These metrics establish reliability targets and measurement methods for the system.

### Definitions

- **SLI (Service Level Indicator)**: A quantitative measure of service reliability
- **SLO (Service Level Objective)**: Target value or range for an SLI
- **Error Budget**: Allowed failure rate (100% - SLO target)

### Purpose

1. Define clear reliability expectations
2. Provide measurable success criteria
3. Guide operational priorities
4. Enable data-driven decision making
5. Support incident severity classification

---

## SLI/SLO Summary Table

| SLI | SLO Target | Measurement Window | Error Budget | Alert Threshold |
|-----|------------|-------------------|--------------|-----------------|
| **Sync Success Rate** | ≥ 95% | 7 days | 5% | < 95% for 1h |
| **Sync Availability** | ≥ 99% | 30 days | 1% | No sync in 8h |
| **Sync Duration (p95)** | ≤ 60 seconds | 7 days | N/A | > 60s for 30m |
| **Data Freshness** | ≤ 8 hours | N/A | 2h buffer | > 8h stale |
| **Admin Endpoint Uptime** | ≥ 99.9% | 30 days | 0.1% | < 99.9% for 1h |

---

## Detailed SLI/SLO Definitions

### 1. Sync Success Rate

**Description**: Percentage of scheduled sync runs that complete successfully

**SLI Definition**:
```promql
sum(rate(pricing_scheduled_sync_runs_total{status="success"}[7d]))
/ sum(rate(pricing_scheduled_sync_runs_total[7d]))
* 100
```

**SLO Target**: ≥ 95%

**Rationale**:
- 95% allows for occasional transient failures
- Provides 5% error budget for provider outages, network issues
- Balances reliability with operational flexibility

**Measurement Window**: 7 days (rolling)

**Error Budget**: 5% of syncs can fail
- Example: 28 syncs/week (4 per day) → 1.4 failures allowed

**Alert Thresholds**:
- **Warning**: Success rate < 95% for 30 minutes
- **Critical**: Success rate < 90% for 15 minutes

**Dependencies**:
- Provider API availability
- Database availability
- Network connectivity
- Application health

**Impact of Miss**:
- Pricing data becomes increasingly stale
- Manual intervention required
- Potential billing inaccuracies

---

### 2. Sync Availability

**Description**: Percentage of time that scheduled syncs are running and completing

**SLI Definition**:
```promql
# Uptime calculation
(
  (time() - pricing_last_sync_timestamp < 28800)
  / (time() - pricing_first_sync_timestamp)
) * 100

# Alternative: Count successful syncs in window
increase(pricing_scheduled_sync_runs_total{status="success"}[30d])
/ (30 * 24 / SYNC_INTERVAL_HOURS)
* 100
```

**SLO Target**: ≥ 99%

**Rationale**:
- 99% uptime = ~7.2 hours downtime per month
- Accounts for brief outages and maintenance
- Higher than success rate because includes partial availability

**Measurement Window**: 30 days (rolling)

**Error Budget**: 1% downtime = ~7.2 hours/month

**Alert Thresholds**:
- **Warning**: No sync in 7 hours (approaching threshold)
- **Critical**: No sync in 8 hours (exceeding threshold)

**Dependencies**:
- Scheduler process running
- Application deployed and healthy
- Configuration correct

**Impact of Miss**:
- Scheduler completely stopped
- No pricing updates at all
- Immediate operational response needed

---

### 3. Sync Duration (p95)

**Description**: 95th percentile of sync duration over measurement window

**SLI Definition**:
```promql
histogram_quantile(0.95,
  rate(pricing_scheduled_sync_duration_seconds_bucket[7d])
)
```

**SLO Target**: ≤ 60 seconds

**Rationale**:
- Expected sync takes 10-30 seconds normally
- 60 second threshold allows for occasional slowness
- p95 means 5% of syncs can be slower
- Prevents timeout risks (typical timeout: 120 seconds)

**Measurement Window**: 7 days (rolling)

**Error Budget**: N/A (performance metric, not availability)

**Alert Thresholds**:
- **Warning**: p95 > 60s for 30 minutes
- **Info**: Average > 45s for 1 hour

**Dependencies**:
- Provider API response times
- Database query performance
- Network latency
- System resource availability

**Impact of Miss**:
- Increased resource consumption
- Risk of timeouts
- Potential for cascading delays
- Higher infrastructure costs

---

### 4. Data Freshness

**Description**: Maximum age of pricing data (time since last successful sync)

**SLI Definition**:
```promql
time() - max(pricing_last_sync_timestamp)
```

**SLO Target**: ≤ 8 hours

**Rationale**:
- Expected sync interval: 6 hours
- 2 hour grace period for transient issues
- Allows missing one sync without violation
- Balances freshness with operational flexibility

**Measurement Window**: N/A (point-in-time measurement)

**Error Budget**: 2 hours beyond sync interval

**Alert Thresholds**:
- **Info**: Data age > 6.5 hours (approaching staleness)
- **Warning**: Data age > 8 hours (SLO violation)
- **Critical**: Data age > 12 hours (severe staleness)

**Dependencies**:
- Sync success rate
- Sync availability
- Provider data currency

**Impact of Miss**:
- Outdated pricing displayed to customers
- Billing inaccuracies
- Competitive disadvantage

---

### 5. Admin Endpoint Uptime

**Description**: Availability of admin control endpoints for scheduler management

**SLI Definition**:
```promql
sum(rate(http_requests_total{
  endpoint=~"/admin/pricing/scheduler/.*",
  status_code!~"5.."
}[30d]))
/ sum(rate(http_requests_total{
  endpoint=~"/admin/pricing/scheduler/.*"
}[30d]))
* 100
```

**SLO Target**: ≥ 99.9%

**Rationale**:
- Admin endpoints critical for incident response
- 99.9% = ~43 minutes downtime per month
- Higher than scheduler itself (needed to fix issues)
- Reflects application-level availability

**Measurement Window**: 30 days (rolling)

**Error Budget**: 0.1% downtime = ~43 minutes/month

**Alert Thresholds**:
- **Warning**: Error rate > 1% for 15 minutes
- **Critical**: Error rate > 5% for 5 minutes

**Dependencies**:
- Application health
- Authentication service
- Database availability
- Network connectivity

**Impact of Miss**:
- Cannot monitor scheduler status
- Cannot trigger manual syncs
- Reduced operational capability
- Longer incident response time

---

## Composite SLOs

### Overall Scheduler Reliability

**Definition**: Combined measure of all SLIs

**Target**: All individual SLOs met simultaneously

**Calculation**:
```
Scheduler Reliability = (
  (Sync Success Rate ≥ 95%) AND
  (Sync Availability ≥ 99%) AND
  (Sync Duration p95 ≤ 60s) AND
  (Data Freshness ≤ 8h) AND
  (Admin Endpoint Uptime ≥ 99.9%)
)
```

**Reporting**: Monthly reliability report

---

## Error Budget Policy

### Error Budget Calculation

```
Error Budget Remaining = SLO Target - Actual Performance

Example:
- SLO Target: 95% success rate
- Actual: 96% success rate
- Error Budget Used: 1% (leaving 4% available)
- Error Budget Remaining: 80% of budget available
```

### Error Budget Actions

| Error Budget Remaining | Action |
|------------------------|--------|
| **75-100%** | Normal operations, focus on features |
| **50-75%** | Caution, monitor closely |
| **25-50%** | Focus on reliability, reduce changes |
| **0-25%** | Incident mode, freeze new features |
| **< 0%** | SLO violation, post-mortem required |

### Error Budget Reset

- Error budgets reset monthly
- Rolling window for continuous assessment
- Historical tracking for trend analysis

---

## Monitoring Dashboard Requirements

### Required Metrics Display

1. **SLO Status Panel**
   - Current SLO compliance (✅/❌)
   - Error budget remaining (%)
   - Trend direction (↑/↓/→)

2. **SLI Time Series**
   - All SLIs over last 7/30 days
   - SLO threshold lines
   - Violation markers

3. **Error Budget Burn Rate**
   - Current burn rate
   - Projected budget exhaustion
   - Historical burn patterns

4. **Detailed Breakdowns**
   - Per-provider success rates
   - Sync duration distribution
   - Failure reason categorization

---

## SLO Reporting

### Daily Report

**To**: Platform Team (Slack)

**Content**:
- All SLI current values
- SLO compliance status (✅/❌)
- Error budget remaining
- Yesterday's incidents

### Weekly Report

**To**: Platform Team + Engineering Leadership

**Content**:
- 7-day SLI trends
- SLO compliance summary
- Error budget analysis
- Top failure reasons
- Performance optimization suggestions

### Monthly Report

**To**: Engineering + Product + Executive Leadership

**Content**:
- 30-day SLO compliance
- Error budget usage and trends
- Major incidents summary
- Reliability improvements implemented
- Upcoming reliability initiatives
- Business impact assessment

---

## SLO Review Process

### Quarterly Review

**Purpose**: Assess if SLOs are appropriate

**Questions to Answer**:
1. Are SLOs too strict or too lenient?
2. Have dependencies changed?
3. Are error budgets being used appropriately?
4. Do SLOs align with business needs?
5. Are measurements accurate?

**Potential Adjustments**:
- Tighten SLOs if consistently exceeded
- Loosen SLOs if impossible to meet
- Add new SLIs for emerging concerns
- Remove SLIs that aren't useful

**Approval Required**: Engineering Lead + Product Owner

---

## Alerting Strategy

### Alert Severity Mapping

| SLO Violation Risk | Severity | Response Time | Example |
|-------------------|----------|---------------|---------|
| **Immediate** | Critical | 15 minutes | Scheduler stopped |
| **Near-term** | Warning | 1 hour | Error rate elevated |
| **Long-term** | Info | Next business day | Slow performance |

### Alert Fatigue Prevention

1. **Threshold Tuning**: Adjust to minimize false positives
2. **Aggregation**: Group related alerts
3. **Suppression**: Silence during known maintenance
4. **Actionability**: Every alert must have clear action
5. **Escalation**: Auto-escalate if not acknowledged

---

## SLO Exceptions

### Planned Exceptions

**Scenarios**:
- Scheduled maintenance windows
- Planned provider migrations
- System upgrades
- Database migrations

**Process**:
1. Document exception in advance
2. Notify stakeholders
3. Exclude from SLO calculations
4. Document actual vs expected impact

### Unplanned Exceptions

**Scenarios**:
- Provider outages beyond our control
- Cloud provider incidents
- DDoS attacks
- Natural disasters

**Process**:
1. Document incident
2. Assess if SLO violation was unavoidable
3. Determine if exception warranted
4. Learn and improve for future

---

## Improvement Initiatives

### When SLOs Are Not Met

1. **Immediate**: Restore service
2. **Short-term**: Implement quick fixes
3. **Medium-term**: Address root causes
4. **Long-term**: Architectural improvements

### When SLOs Are Consistently Exceeded

1. **Assess**: Why are we over-delivering?
2. **Optimize**: Can we reduce cost while maintaining SLO?
3. **Tighten**: Should we raise the bar?
4. **Invest**: Redirect effort to other priorities

---

## Dependency SLOs

### Provider API SLOs

**Expected**:
- Availability: ≥ 99.9%
- Response Time: ≤ 2 seconds (p95)
- Error Rate: ≤ 1%

**Impact on Our SLOs**:
- Provider availability directly affects sync success rate
- Provider performance affects sync duration
- Multiple providers provide redundancy

### Database SLOs

**Expected**:
- Availability: ≥ 99.99%
- Query Time: ≤ 100ms (p95)
- Connection Pool: Always available

**Impact on Our SLOs**:
- Database availability affects sync success rate
- Database performance affects sync duration
- Database downtime stops all syncs

### Application SLOs

**Expected**:
- Uptime: ≥ 99.9%
- API Response Time: ≤ 500ms (p95)
- Background Task Processing: Always running

**Impact on Our SLOs**:
- Application uptime affects scheduler availability
- Background task system affects sync execution

---

## Metrics Collection

### Required Prometheus Metrics

```promql
# Already implemented:
- pricing_scheduled_sync_runs_total{status="success|failed"}
- pricing_scheduled_sync_duration_seconds_bucket
- pricing_last_sync_timestamp{provider}
- pricing_models_synced_total{provider}
- pricing_manual_sync_runs_total

# To be implemented:
- pricing_sync_error_reasons_total{reason}
- pricing_provider_response_time_seconds{provider}
- pricing_database_query_duration_seconds
- pricing_models_update_errors_total
```

### Data Retention

- **High Resolution (1m)**: 30 days
- **Medium Resolution (5m)**: 90 days
- **Low Resolution (1h)**: 1 year
- **Aggregated Reports**: 3 years

---

## Success Criteria

**Phase 6 is successful when**:

1. ✅ All SLOs defined and documented
2. ✅ SLI metrics collection implemented
3. ✅ Monitoring dashboards show SLO status
4. ✅ Alerts configured for SLO violations
5. ✅ Error budget tracking operational
6. ✅ Reporting cadence established
7. ✅ Team trained on SLO framework

---

## References

- **Phase 5 Deployment Guide**: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- **Alert Rules**: `monitoring/prometheus/pricing_sync_alerts.yml`
- **Dashboards**: `monitoring/grafana/pricing_sync_*.json`
- **Runbooks**: `docs/runbooks/pricing_sync_*.md`
- **Google SRE Book**: https://sre.google/sre-book/service-level-objectives/

---

## Appendix: Calculation Examples

### Example 1: Sync Success Rate

**Scenario**: 7 days, 4 syncs/day, 2 failures

```
Total syncs: 7 days × 4 syncs/day = 28 syncs
Successful syncs: 28 - 2 = 26 syncs
Success rate: 26 / 28 = 92.86%
SLO target: 95%
Result: ❌ SLO violated
Error budget used: 7.14% (budget was 5%)
```

### Example 2: Data Freshness

**Scenario**: Last sync 7.5 hours ago

```
Last sync timestamp: 1737900000 (7.5 hours ago)
Current time: 1737927000
Age: 27000 seconds = 7.5 hours
SLO target: 8 hours
Result: ✅ SLO met (within threshold)
Buffer remaining: 0.5 hours
```

### Example 3: Error Budget Burn Rate

**Scenario**: 95% SLO, currently at 93%

```
SLO target: 95%
Current performance: 93%
Error budget: 5%
Budget used: 2% (95% - 93%)
Budget remaining: 3%
Burn rate: 40% of budget used (2% / 5%)
```

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Owner**: Platform Team
**Next Review**: 2026-04-26
