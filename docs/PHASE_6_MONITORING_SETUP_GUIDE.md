# Phase 6: Monitoring & Alerts Setup Guide

**Phase**: 6 (Monitoring & Alerts)
**Status**: Implementation Ready
**Date**: 2026-01-26
**Owner**: Platform Team

---

## Overview

This guide provides step-by-step instructions for setting up comprehensive monitoring and alerting for the automated pricing sync scheduler. After completing this guide, you will have:

- ✅ Prometheus collecting all pricing sync metrics
- ✅ Grafana dashboards visualizing scheduler health and system impact
- ✅ Alert rules configured for critical and warning conditions
- ✅ Runbooks ready for incident response
- ✅ SLO/SLI tracking operational

---

## Prerequisites

**Before starting**:
- ✅ Phase 5 deployed to production
- ✅ Scheduler running stably for 1+ week
- ✅ Initial metrics collected in Prometheus
- ✅ Baseline performance established

**Required Access**:
- Prometheus admin access
- Grafana admin access
- Alertmanager configuration access
- PagerDuty/Slack webhook access (if using)
- Railway/hosting admin access

**Required Tools**:
- `curl` - Testing endpoints
- `jq` - JSON parsing
- `promtool` - Validating Prometheus rules
- Git - Version control

---

## Setup Timeline

**Estimated Time**: 4-6 hours

| Task | Duration | Dependencies |
|------|----------|--------------|
| 1. Prometheus Setup | 1 hour | Prometheus access |
| 2. Grafana Dashboards | 1 hour | Grafana access |
| 3. Alert Rules | 1 hour | Prometheus + Alertmanager |
| 4. Alert Routing | 1 hour | Slack/PagerDuty setup |
| 5. Verification | 1 hour | All above |
| 6. Documentation | 30 min | - |

---

## Part 1: Prometheus Setup

### Step 1.1: Verify Metrics Collection

```bash
# Check that application is exposing metrics
curl https://api.gatewayz.ai/metrics | grep pricing_

# You should see metrics like:
# pricing_scheduled_sync_runs_total{status="success"} 42
# pricing_scheduled_sync_duration_seconds_sum 530.5
# pricing_last_sync_timestamp{provider="openrouter"} 1737900000.0
```

**If no metrics**:
- Verify scheduler is running
- Check metrics endpoint is enabled
- Review Phase 2.5 implementation

### Step 1.2: Configure Prometheus Scraping

**Add scrape configuration to prometheus.yml**:

```yaml
scrape_configs:
  - job_name: 'gatewayz-api'
    scrape_interval: 30s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    static_configs:
      - targets: ['api.gatewayz.ai:443']
        labels:
          env: 'production'
          component: 'api'
    scheme: https

  - job_name: 'gatewayz-api-staging'
    scrape_interval: 30s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    static_configs:
      - targets: ['gatewayz-staging.up.railway.app:443']
        labels:
          env: 'staging'
          component: 'api'
    scheme: https
```

### Step 1.3: Reload Prometheus Configuration

```bash
# Validate configuration first
promtool check config /path/to/prometheus.yml

# Reload Prometheus (method depends on deployment)
# Option A: Send reload signal
curl -X POST http://localhost:9090/-/reload

# Option B: Restart Prometheus
systemctl restart prometheus

# Option C: Docker
docker restart prometheus
```

### Step 1.4: Verify Scraping

```bash
# Check Prometheus targets page
open http://localhost:9090/targets

# Verify gatewayz-api target is UP
# Check last scrape time is recent (< 1 minute ago)
# Verify no scrape errors
```

### Step 1.5: Test Queries

```bash
# Open Prometheus query interface
open http://localhost:9090/graph

# Test queries:
pricing_scheduled_sync_runs_total
rate(pricing_scheduled_sync_duration_seconds_count[5m])
time() - pricing_last_sync_timestamp
```

**Expected**: All queries return data

---

## Part 2: Grafana Dashboards

### Step 2.1: Import Health Dashboard

```bash
# Copy dashboard JSON to local machine
cp monitoring/grafana/pricing_sync_scheduler_health.json /tmp/

# Option A: Import via Grafana UI
# 1. Navigate to Grafana → Dashboards → Import
# 2. Upload pricing_sync_scheduler_health.json
# 3. Select Prometheus datasource
# 4. Click Import

# Option B: Import via API
curl -X POST http://admin:password@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @monitoring/grafana/pricing_sync_scheduler_health.json
```

### Step 2.2: Import System Impact Dashboard

```bash
# Import system impact dashboard
curl -X POST http://admin:password@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @monitoring/grafana/pricing_sync_system_impact.json
```

### Step 2.3: Configure Data Source

**In Grafana**:
1. Go to Configuration → Data Sources
2. Add Prometheus data source if not exists
3. Set URL: `http://prometheus:9090` (or your Prometheus URL)
4. Set Access: Server (default)
5. Save & Test

### Step 2.4: Verify Dashboards

```bash
# Open Health Dashboard
open http://localhost:3000/d/pricing-sync-health

# Verify:
# - All panels loading data
# - No "No Data" messages
# - Recent data visible (last 6 hours)
# - Metrics match expected values

# Open System Impact Dashboard
open http://localhost:3000/d/pricing-sync-system-impact

# Verify:
# - CPU/Memory panels showing data
# - Database metrics visible
# - API response times displayed
```

### Step 2.5: Create Dashboard Folder

```bash
# Organize dashboards
# In Grafana UI:
# 1. Create folder "Pricing Sync"
# 2. Move both dashboards to folder
# 3. Set folder permissions (Platform Team)
```

### Step 2.6: Configure Dashboard Variables (Optional)

**Add environment variable**:
1. Dashboard Settings → Variables → Add Variable
2. Name: `environment`
3. Type: Query
4. Query: `label_values(env)`
5. Multi-value: enabled
6. Include All: enabled
7. Save

**Update queries to use variable**:
```promql
pricing_scheduled_sync_runs_total{env=~"$environment"}
```

---

## Part 3: Alert Rules Configuration

### Step 3.1: Copy Alert Rules

```bash
# Copy alert rules to Prometheus directory
cp monitoring/prometheus/pricing_sync_alerts.yml /etc/prometheus/rules/

# Or if using Docker:
docker cp monitoring/prometheus/pricing_sync_alerts.yml prometheus:/etc/prometheus/rules/
```

### Step 3.2: Update Prometheus Configuration

**Add rule file to prometheus.yml**:

```yaml
rule_files:
  - "rules/pricing_sync_alerts.yml"
```

### Step 3.3: Validate Alert Rules

```bash
# Validate rules file
promtool check rules /etc/prometheus/rules/pricing_sync_alerts.yml

# Should output:
# Checking /etc/prometheus/rules/pricing_sync_alerts.yml
#   SUCCESS: 10 rules found
```

### Step 3.4: Reload Prometheus

```bash
# Reload to apply new rules
curl -X POST http://localhost:9090/-/reload

# Or restart
systemctl restart prometheus
```

### Step 3.5: Verify Alert Rules

```bash
# Open Prometheus alerts page
open http://localhost:9090/alerts

# Verify:
# - All alert rules listed
# - Rules in "Inactive" state (green) if no issues
# - No evaluation errors
```

**Expected alerts**:
- PricingSyncSchedulerStopped
- PricingSyncHighErrorRate
- PricingSyncNoRunsRecorded
- PricingSyncDatabaseUpdateFailures
- PricingSyncSlowDuration
- PricingSyncLowModelsUpdated
- PricingSyncMemoryUsageHigh
- PricingSyncProviderTimeout
- PricingSyncStaleData

### Step 3.6: Test Alert Conditions (Optional)

```bash
# Temporarily trigger alert by stopping scheduler
railway variables set PRICING_SYNC_ENABLED=false --environment staging
railway redeploy --environment staging

# Wait 8+ hours (or adjust alert threshold for testing)

# Verify alert fires
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname=="PricingSyncSchedulerStopped")'

# Re-enable scheduler
railway variables set PRICING_SYNC_ENABLED=true --environment staging
railway redeploy --environment staging
```

---

## Part 4: Alertmanager Configuration

### Step 4.1: Install Alertmanager (if not installed)

```bash
# Docker
docker run -d \
  --name alertmanager \
  -p 9093:9093 \
  -v /path/to/alertmanager.yml:/etc/alertmanager/alertmanager.yml \
  prom/alertmanager

# Or via package manager
# apt-get install prometheus-alertmanager
```

### Step 4.2: Configure Alertmanager

**Create/update alertmanager.yml**:

```yaml
global:
  resolve_timeout: 5m
  slack_api_url: 'YOUR_SLACK_WEBHOOK_URL'

route:
  group_by: ['alertname', 'component']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    # Critical alerts to PagerDuty + Slack
    - match:
        severity: critical
        component: pricing_sync
      receiver: 'pagerduty-platform'
      continue: true

    - match:
        severity: critical
        component: pricing_sync
      receiver: 'slack-critical'

    # Warning alerts to Slack only
    - match:
        severity: warning
        component: pricing_sync
      receiver: 'slack-warnings'

    # Info alerts to null (logged only)
    - match:
        severity: info
      receiver: 'null'

receivers:
  - name: 'default'
    slack_configs:
      - channel: '#platform-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'pagerduty-platform'
    pagerduty_configs:
      - service_key: 'YOUR_PAGERDUTY_SERVICE_KEY'
        description: '{{ .GroupLabels.alertname }}: {{ .GroupLabels.component }}'

  - name: 'slack-critical'
    slack_configs:
      - channel: '#platform-critical'
        title: '🚨 CRITICAL: {{ .GroupLabels.alertname }}'
        text: |
          *Alert*: {{ .GroupLabels.alertname }}
          *Severity*: {{ .CommonLabels.severity }}
          *Component*: {{ .CommonLabels.component }}
          *Description*: {{ .CommonAnnotations.description }}
          *Action*: {{ .CommonAnnotations.action }}
          *Runbook*: <{{ .CommonAnnotations.runbook }}|View Runbook>
        color: 'danger'
        send_resolved: true

  - name: 'slack-warnings'
    slack_configs:
      - channel: '#platform-warnings'
        title: '⚠️ WARNING: {{ .GroupLabels.alertname }}'
        text: |
          *Alert*: {{ .GroupLabels.alertname }}
          *Severity*: {{ .CommonLabels.severity }}
          *Component*: {{ .CommonLabels.component }}
          *Description*: {{ .CommonAnnotations.description }}
          *Action*: {{ .CommonAnnotations.action }}
        color: 'warning'
        send_resolved: true

  - name: 'null'

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'component']
```

### Step 4.3: Update Prometheus to Use Alertmanager

**In prometheus.yml**:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']
```

### Step 4.4: Reload Configurations

```bash
# Validate Alertmanager config
amtool check-config /etc/alertmanager/alertmanager.yml

# Reload Alertmanager
curl -X POST http://localhost:9093/-/reload

# Reload Prometheus
curl -X POST http://localhost:9090/-/reload
```

### Step 4.5: Test Alert Routing

```bash
# Send test alert
amtool alert add test_alert severity=warning component=pricing_sync \
  --alertmanager.url=http://localhost:9093

# Verify alert received in Slack
# Check #platform-warnings channel for test alert

# Resolve test alert
amtool alert add test_alert severity=warning component=pricing_sync \
  endsAt=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --alertmanager.url=http://localhost:9093
```

---

## Part 5: Slack Integration

### Step 5.1: Create Slack Webhooks

**Create webhooks for each channel**:

1. Go to https://api.slack.com/apps
2. Create new app or select existing
3. Enable Incoming Webhooks
4. Add webhooks for:
   - `#platform-alerts` (default)
   - `#platform-critical` (critical alerts)
   - `#platform-warnings` (warnings)
5. Copy webhook URLs

### Step 5.2: Update Alertmanager Config

```bash
# Replace placeholder with actual webhook URL
sed -i 's|YOUR_SLACK_WEBHOOK_URL|https://hooks.slack.com/services/...|g' /etc/alertmanager/alertmanager.yml

# Reload
curl -X POST http://localhost:9093/-/reload
```

### Step 5.3: Test Slack Notifications

```bash
# Send test notification
curl -X POST YOUR_SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Test notification from Pricing Sync Monitoring",
    "channel": "#platform-alerts"
  }'

# Verify message appears in Slack
```

---

## Part 6: PagerDuty Integration (Optional)

### Step 6.1: Create PagerDuty Service

1. Log in to PagerDuty
2. Go to Configuration → Services
3. Create new service:
   - Name: "Gatewayz Pricing Sync"
   - Escalation Policy: Select appropriate
   - Integration: Prometheus
4. Copy integration key

### Step 6.2: Update Alertmanager Config

```bash
# Add PagerDuty integration key
sed -i 's|YOUR_PAGERDUTY_SERVICE_KEY|YOUR_ACTUAL_KEY|g' /etc/alertmanager/alertmanager.yml

# Reload
curl -X POST http://localhost:9093/-/reload
```

### Step 6.3: Test PagerDuty Integration

```bash
# Trigger critical test alert
amtool alert add pagerduty_test \
  severity=critical \
  component=pricing_sync \
  alertname="PagerDuty Test Alert" \
  --alertmanager.url=http://localhost:9093

# Verify:
# - Incident created in PagerDuty
# - On-call person paged
# - Alert shows in PagerDuty dashboard
```

---

## Part 7: Runbook Deployment

### Step 7.1: Publish Runbooks

**Option A: Internal Documentation System**

```bash
# If using internal docs (e.g., Confluence, Notion)
# Export runbooks to your documentation platform
# Create links from alerts to runbooks
```

**Option B: GitHub Repository**

```bash
# Runbooks already in docs/runbooks/
# Ensure repository is accessible to on-call team
# Create short links for easy access

# Example:
docs.gatewayz.ai/runbooks/pricing-sync-stopped
```

### Step 7.2: Update Alert Annotations

**Ensure alert rules include runbook URLs**:

```yaml
annotations:
  runbook: https://docs.gatewayz.ai/runbooks/pricing-sync-stopped
```

### Step 7.3: Train Team on Runbooks

1. Schedule runbook review session
2. Walk through each runbook
3. Practice resolution steps
4. Document any questions or improvements

---

## Part 8: SLO Tracking Setup

### Step 8.1: Create SLO Dashboard in Grafana

**Option A: Manual Creation**

1. Create new dashboard "Pricing Sync SLOs"
2. Add panels for each SLI:
   - Sync Success Rate
   - Sync Availability
   - Sync Duration (p95)
   - Data Freshness
   - Admin Endpoint Uptime
3. Add SLO threshold lines
4. Add error budget panels

**Option B: Use Template**

```bash
# Create SLO dashboard from template
# (Template to be created based on PRICING_SYNC_SLOS.md)
```

### Step 8.2: Configure SLO Recording Rules

**Add to prometheus.yml**:

```yaml
rule_files:
  - "rules/pricing_sync_alerts.yml"
  - "rules/pricing_sync_slo_recording.yml"  # New file
```

**Create pricing_sync_slo_recording.yml**:

```yaml
groups:
  - name: pricing_sync_slo_recording
    interval: 60s
    rules:
      # Sync Success Rate SLI
      - record: pricing_sync:sli:success_rate:7d
        expr: |
          sum(rate(pricing_scheduled_sync_runs_total{status="success"}[7d]))
          / sum(rate(pricing_scheduled_sync_runs_total[7d]))
          * 100

      # Sync Availability SLI
      - record: pricing_sync:sli:availability:30d
        expr: |
          (time() - pricing_last_sync_timestamp < 28800) * 100

      # Sync Duration p95 SLI
      - record: pricing_sync:sli:duration_p95:7d
        expr: |
          histogram_quantile(0.95,
            rate(pricing_scheduled_sync_duration_seconds_bucket[7d])
          )

      # Data Freshness SLI
      - record: pricing_sync:sli:data_freshness
        expr: |
          time() - max(pricing_last_sync_timestamp)

      # Error Budget Remaining
      - record: pricing_sync:error_budget:remaining:7d
        expr: |
          (95 - pricing_sync:sli:success_rate:7d) / 5 * 100
```

### Step 8.3: Verify SLO Metrics

```bash
# Check recording rules
curl http://localhost:9090/api/v1/rules | jq '.data.groups[] | select(.name=="pricing_sync_slo_recording")'

# Query SLI metrics
curl -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=pricing_sync:sli:success_rate:7d'
```

---

## Part 9: Verification & Testing

### Step 9.1: End-to-End Verification Checklist

```bash
# Metrics Collection
[ ] Prometheus scraping metrics successfully
[ ] All pricing_* metrics visible
[ ] Metrics updating every 30 seconds

# Dashboards
[ ] Health dashboard loads without errors
[ ] System impact dashboard loads
[ ] All panels showing recent data
[ ] SLO dashboard displaying correctly

# Alerts
[ ] All alert rules loaded in Prometheus
[ ] Alerts in inactive state (no current issues)
[ ] Alert annotations include runbook links

# Alert Routing
[ ] Alertmanager receiving alerts from Prometheus
[ ] Slack integration working
[ ] PagerDuty integration working (if configured)
[ ] Alert suppression rules working

# Runbooks
[ ] All runbooks accessible to team
[ ] Runbook links working in alerts
[ ] Team trained on runbook usage

# SLO Tracking
[ ] SLO recording rules active
[ ] SLI metrics collecting
[ ] Error budget calculated correctly
```

### Step 9.2: Simulate Alert Scenarios

**Test 1: Scheduler Stopped Alert**

```bash
# Stop scheduler
railway variables set PRICING_SYNC_ENABLED=false --environment staging

# Wait for alert threshold (8 hours or adjust for testing)
# Verify alert fires
# Verify notification received
# Follow runbook to resolve
# Verify alert resolves

# Re-enable
railway variables set PRICING_SYNC_ENABLED=true --environment staging
```

**Test 2: High Error Rate Alert**

```bash
# Intentionally cause failures (e.g., invalid API key temporarily)
# Wait for error rate threshold
# Verify alert fires
# Follow runbook
# Resolve issue
# Verify alert clears
```

**Test 3: Slow Performance Alert**

```bash
# Monitor performance during high load
# If naturally slow, verify alert fires
# Follow runbook steps
# Document resolution
```

### Step 9.3: Load Testing (Optional)

```bash
# Stress test to verify monitoring under load
# Trigger multiple manual syncs
for i in {1..10}; do
  curl -X POST \
    -H "Authorization: Bearer $ADMIN_API_KEY" \
    https://api.gatewayz.ai/admin/pricing/scheduler/trigger &
done

# Monitor:
# - Dashboard metrics
# - Alert thresholds
# - System resources
# - Alert noise level
```

---

## Part 10: Documentation & Handoff

### Step 10.1: Create Monitoring Playbook

**Document**:
- How to access dashboards
- How to acknowledge alerts
- How to silence alerts (maintenance)
- How to escalate issues
- Emergency contacts

### Step 10.2: Schedule Training

**Topics**:
1. Overview of monitoring stack
2. Dashboard walkthrough
3. Alert response procedures
4. Runbook usage
5. SLO tracking and reporting
6. Q&A session

### Step 10.3: Create Quick Reference Card

```markdown
# Pricing Sync Monitoring Quick Reference

## Dashboards
- Health: https://grafana.gatewayz.ai/d/pricing-sync-health
- System Impact: https://grafana.gatewayz.ai/d/pricing-sync-system-impact
- SLOs: https://grafana.gatewayz.ai/d/pricing-sync-slos

## Runbooks
- Scheduler Stopped: docs.gatewayz.ai/runbooks/pricing-sync-stopped
- High Error Rate: docs.gatewayz.ai/runbooks/pricing-sync-high-error-rate
- Slow Performance: docs.gatewayz.ai/runbooks/pricing-sync-slow-performance

## Key Metrics
- Success Rate: pricing_scheduled_sync_runs_total{status="success"}
- Last Sync: pricing_last_sync_timestamp
- Duration: pricing_scheduled_sync_duration_seconds

## Emergency Contacts
- On-call: Use PagerDuty
- Platform Lead: [contact]
- Engineering Manager: [contact]
```

---

## Maintenance

### Weekly Tasks
- Review alert noise (false positives)
- Check SLO compliance
- Review error budget burn rate
- Test manual alert triggering

### Monthly Tasks
- Review and update runbooks
- Update dashboard layouts
- Optimize alert thresholds
- SLO performance review
- Team feedback session

### Quarterly Tasks
- SLO review and adjustment
- Comprehensive system review
- Monitoring stack updates
- Training refresher

---

## Troubleshooting

### Issue: Metrics Not Appearing

**Diagnosis**:
```bash
# Check if metrics endpoint is accessible
curl https://api.gatewayz.ai/metrics

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets
```

**Resolution**:
- Verify application is running
- Check metrics endpoint configuration
- Verify Prometheus scrape config
- Check network connectivity

### Issue: Alerts Not Firing

**Diagnosis**:
```bash
# Check alert evaluation
curl http://localhost:9090/api/v1/rules

# Check Alertmanager
curl http://localhost:9093/api/v1/alerts
```

**Resolution**:
- Verify alert rules loaded
- Check alert conditions met
- Verify Alertmanager connection
- Check alert routing configuration

### Issue: Slack Notifications Not Working

**Diagnosis**:
```bash
# Test webhook directly
curl -X POST YOUR_SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{"text": "Test"}'
```

**Resolution**:
- Verify webhook URL correct
- Check Slack app permissions
- Verify Alertmanager config
- Check Alertmanager logs

---

## Success Criteria

**Phase 6 setup is complete when**:

1. ✅ Prometheus collecting all metrics
2. ✅ All dashboards operational
3. ✅ All alert rules active
4. ✅ Alert routing configured
5. ✅ Notifications reaching Slack/PagerDuty
6. ✅ Runbooks accessible
7. ✅ SLO tracking operational
8. ✅ Team trained and ready
9. ✅ End-to-end testing completed
10. ✅ Documentation complete

---

## Next Steps

After completing setup:

1. Monitor system for 1 week
2. Fine-tune alert thresholds
3. Gather team feedback
4. Iterate on dashboards
5. Update runbooks based on real incidents
6. Plan Phase 7 (if applicable)

---

## Support

**Questions or Issues**:
- Slack: #platform-team
- Email: platform@gatewayz.ai
- On-call: PagerDuty escalation

---

## References

- **Phase 5 Deployment**: `docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- **Phase 4 Testing**: `docs/PHASE_4_COMPLETION.md`
- **Alert Rules**: `monitoring/prometheus/pricing_sync_alerts.yml`
- **Dashboards**: `monitoring/grafana/pricing_sync_*.json`
- **Runbooks**: `docs/runbooks/pricing_sync_*.md`
- **SLOs**: `docs/PRICING_SYNC_SLOS.md`

---

**Last Updated**: 2026-01-26
**Version**: 1.0
**Author**: Platform Team
