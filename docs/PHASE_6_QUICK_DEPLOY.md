# Phase 6 Monitoring - Quick Deployment Guide

**Last Updated**: 2026-01-26
**Time Required**: 2-3 hours
**Difficulty**: Intermediate

---

## Overview

This guide will help you deploy the Phase 6 monitoring infrastructure to **actual Prometheus and Grafana instances**. Choose your deployment method below.

---

## Deployment Options

### Option 1: Using Grafana Cloud + Prometheus (Recommended for Quick Start)
**Pros**: Free tier available, fully managed, no infrastructure needed
**Time**: ~1 hour

### Option 2: Self-Hosted with Docker
**Pros**: Full control, no vendor lock-in
**Time**: ~2 hours

### Option 3: Using Existing Prometheus/Grafana
**Pros**: Leverage existing infrastructure
**Time**: ~30 minutes

---

## Option 1: Grafana Cloud (Easiest & Fastest)

### Step 1: Sign Up for Grafana Cloud

```bash
# Go to: https://grafana.com/auth/sign-up/create-user
# Free tier includes:
# - Grafana dashboards
# - Prometheus metrics (10K series)
# - Alerts
```

### Step 2: Set Up Prometheus Remote Write

Once signed in to Grafana Cloud:

1. Go to **"Connections" → "Add new connection"**
2. Select **"Hosted Prometheus"**
3. Copy your remote write URL and credentials

You'll get something like:
```
URL: https://prometheus-prod-XX-prod-us-central-0.grafana.net/api/prom/push
Username: <your-instance-id>
Password: <your-api-key>
```

### Step 3: Configure Your Application to Push Metrics

**Option A: Using Prometheus Remote Write (from your app)**

Add to your FastAPI app startup:

```python
# src/config/prometheus_config.py
import os
from prometheus_client import CollectorRegistry, push_to_gateway
from threading import Thread
import time

GRAFANA_CLOUD_URL = os.getenv("GRAFANA_CLOUD_PROMETHEUS_URL")
GRAFANA_CLOUD_USERNAME = os.getenv("GRAFANA_CLOUD_USERNAME")
GRAFANA_CLOUD_PASSWORD = os.getenv("GRAFANA_CLOUD_PASSWORD")

def push_metrics_to_grafana_cloud():
    """Push metrics to Grafana Cloud every 30 seconds"""
    while True:
        try:
            # Your metrics are already being collected
            # Just need to push them
            from prometheus_client import REGISTRY
            from prometheus_client.exposition import push_to_gateway

            push_to_gateway(
                GRAFANA_CLOUD_URL,
                job='gatewayz-api',
                registry=REGISTRY,
                auth=(GRAFANA_CLOUD_USERNAME, GRAFANA_CLOUD_PASSWORD)
            )
        except Exception as e:
            print(f"Error pushing metrics: {e}")

        time.sleep(30)

# Start background thread on app startup
Thread(target=push_metrics_to_grafana_cloud, daemon=True).start()
```

**Option B: Using Grafana Agent (Better for production)**

```bash
# Download Grafana Agent
curl -O -L "https://github.com/grafana/agent/releases/latest/download/grafana-agent-linux-amd64.zip"
unzip grafana-agent-linux-amd64.zip

# Create config file
cat > agent-config.yaml << 'EOF'
server:
  log_level: info

metrics:
  wal_directory: /tmp/grafana-agent-wal
  global:
    scrape_interval: 30s
    remote_write:
      - url: https://prometheus-prod-XX-prod-us-central-0.grafana.net/api/prom/push
        basic_auth:
          username: <your-instance-id>
          password: <your-api-key>

  configs:
    - name: gatewayz
      scrape_configs:
        - job_name: 'gatewayz-api-staging'
          static_configs:
            - targets: ['gatewayz-staging.up.railway.app:443']
          scheme: https
          metrics_path: '/metrics'

        - job_name: 'gatewayz-api-production'
          static_configs:
            - targets: ['api.gatewayz.ai:443']
          scheme: https
          metrics_path: '/metrics'
EOF

# Run the agent
./grafana-agent-linux-amd64 --config.file=agent-config.yaml
```

### Step 4: Import Dashboards to Grafana Cloud

1. **Log in to your Grafana Cloud dashboard**

2. **Import Health Dashboard**:
   - Click **"Dashboards" → "Import"**
   - Click **"Upload JSON file"**
   - Select `monitoring/grafana/pricing_sync_scheduler_health.json`
   - Choose your Prometheus datasource
   - Click **"Import"**

3. **Import System Impact Dashboard**:
   - Repeat above steps with `monitoring/grafana/pricing_sync_system_impact.json`

4. **Verify Dashboards**:
   - Navigate to **"Dashboards"**
   - Click on **"Pricing Sync Scheduler - Health Dashboard"**
   - Wait 1-2 minutes for metrics to appear

### Step 5: Set Up Alerts in Grafana Cloud

1. **Go to "Alerting" → "Alert rules"**

2. **Create a new alert rule**:
   - Click **"New alert rule"**
   - Name: **"Pricing Sync Scheduler Stopped"**
   - Query A: `time() - pricing_last_sync_timestamp > 28800`
   - Condition: When query A is above 0
   - Evaluation: Every 1m for 5m
   - Severity: Critical

3. **Repeat for other critical alerts**:
   - High Error Rate
   - No Runs Recorded
   - Database Update Failures

4. **Set up Contact Points**:
   - Go to **"Alerting" → "Contact points"**
   - Add Slack webhook
   - Add Email notifications

5. **Test an Alert**:
   - Temporarily disable scheduler: `railway variables set PRICING_SYNC_ENABLED=false`
   - Wait 8+ hours or adjust threshold for testing
   - Verify alert fires
   - Re-enable: `railway variables set PRICING_SYNC_ENABLED=true`

---

## Option 2: Self-Hosted with Docker

### Step 1: Create Docker Compose Configuration

```bash
# Create monitoring directory
mkdir -p ~/gatewayz-monitoring
cd ~/gatewayz-monitoring

# Create docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  prometheus:
    image: prom/prometheus:latest
    container_name: gatewayz-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./prometheus/rules:/etc/prometheus/rules
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.enable-lifecycle'
    restart: unless-stopped

  grafana:
    image: grafana/grafana:latest
    container_name: gatewayz-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    restart: unless-stopped
    depends_on:
      - prometheus

  alertmanager:
    image: prom/alertmanager:latest
    container_name: gatewayz-alertmanager
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml
      - alertmanager-data:/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
    restart: unless-stopped

volumes:
  prometheus-data:
  grafana-data:
  alertmanager-data:
EOF
```

### Step 2: Configure Prometheus

```bash
# Create Prometheus config directory
mkdir -p prometheus/rules

# Copy alert rules from your repo
cp /path/to/gatewayz-backend/monitoring/prometheus/pricing_sync_alerts.yml \
   prometheus/rules/

# Create Prometheus config
cat > prometheus/prometheus.yml << 'EOF'
global:
  scrape_interval: 30s
  evaluation_interval: 30s

# Alertmanager configuration
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

# Load alert rules
rule_files:
  - "rules/*.yml"

# Scrape configurations
scrape_configs:
  # Gatewayz API - Staging
  - job_name: 'gatewayz-api-staging'
    scrape_interval: 30s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    scheme: https
    static_configs:
      - targets: ['gatewayz-staging.up.railway.app:443']
        labels:
          env: 'staging'
          service: 'gatewayz-api'

  # Gatewayz API - Production
  - job_name: 'gatewayz-api-production'
    scrape_interval: 30s
    scrape_timeout: 10s
    metrics_path: '/metrics'
    scheme: https
    static_configs:
      - targets: ['api.gatewayz.ai:443']
        labels:
          env: 'production'
          service: 'gatewayz-api'

  # Prometheus self-monitoring
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
EOF
```

### Step 3: Configure Alertmanager

```bash
# Create Alertmanager directory
mkdir -p alertmanager

# Create Alertmanager config (replace with your webhooks)
cat > alertmanager/alertmanager.yml << 'EOF'
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'component']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    # Critical alerts to Slack
    - match:
        severity: critical
        component: pricing_sync
      receiver: 'slack-critical'
      continue: false

    # Warning alerts to Slack
    - match:
        severity: warning
        component: pricing_sync
      receiver: 'slack-warnings'

receivers:
  - name: 'default'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#platform-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'slack-critical'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#platform-critical'
        title: '🚨 CRITICAL: {{ .GroupLabels.alertname }}'
        text: |
          *Severity*: {{ .CommonLabels.severity }}
          *Component*: {{ .CommonLabels.component }}
          *Description*: {{ .CommonAnnotations.description }}
          *Action*: {{ .CommonAnnotations.action }}
        color: 'danger'
        send_resolved: true

  - name: 'slack-warnings'
    slack_configs:
      - api_url: 'YOUR_SLACK_WEBHOOK_URL'
        channel: '#platform-warnings'
        title: '⚠️ WARNING: {{ .GroupLabels.alertname }}'
        text: |
          *Description*: {{ .CommonAnnotations.description }}
          *Action*: {{ .CommonAnnotations.action }}
        color: 'warning'
        send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'component']
EOF
```

### Step 4: Configure Grafana Provisioning

```bash
# Create provisioning directories
mkdir -p grafana/provisioning/datasources
mkdir -p grafana/provisioning/dashboards

# Auto-provision Prometheus datasource
cat > grafana/provisioning/datasources/prometheus.yml << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
EOF

# Auto-provision dashboards
cat > grafana/provisioning/dashboards/pricing-sync.yml << 'EOF'
apiVersion: 1

providers:
  - name: 'Pricing Sync'
    orgId: 1
    folder: 'Pricing Sync Monitoring'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

# Copy dashboard JSONs
cp /path/to/gatewayz-backend/monitoring/grafana/*.json \
   grafana/provisioning/dashboards/
```

### Step 5: Start Everything

```bash
# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f

# Verify Prometheus is running
curl http://localhost:9090/-/healthy

# Verify Grafana is running
curl http://localhost:3000/api/health
```

### Step 6: Access Dashboards

1. **Prometheus**: http://localhost:9090
   - Go to **Status → Targets** to verify scraping
   - Go to **Alerts** to see alert rules

2. **Grafana**: http://localhost:3000
   - Login: `admin` / `admin`
   - Navigate to **Dashboards → Pricing Sync Monitoring**
   - Should see both dashboards pre-loaded

3. **Alertmanager**: http://localhost:9093
   - View active alerts
   - Silence alerts if needed

### Step 7: Set Up Slack Webhooks

```bash
# 1. Create Slack App
# Go to: https://api.slack.com/apps
# Click "Create New App" → "From scratch"

# 2. Enable Incoming Webhooks
# In your app settings:
# - Click "Incoming Webhooks"
# - Toggle "Activate Incoming Webhooks" to On
# - Click "Add New Webhook to Workspace"
# - Select channel (#platform-critical or #platform-warnings)
# - Copy webhook URL

# 3. Update alertmanager.yml
# Replace YOUR_SLACK_WEBHOOK_URL with actual webhook

# 4. Reload Alertmanager
docker-compose restart alertmanager
```

### Step 8: Test Alerts

```bash
# Send test alert to Alertmanager
curl -X POST http://localhost:9093/api/v1/alerts -d '[
  {
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning",
      "component": "pricing_sync"
    },
    "annotations": {
      "description": "This is a test alert",
      "action": "No action needed"
    }
  }
]'

# Check if alert appears in Slack
```

---

## Option 3: Using Existing Prometheus/Grafana

If you already have Prometheus and Grafana running:

### Step 1: Add Scrape Config to Prometheus

Add to your existing `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'gatewayz-api'
    static_configs:
      - targets: ['gatewayz-staging.up.railway.app:443']
    scheme: https
    metrics_path: '/metrics'
```

### Step 2: Copy Alert Rules

```bash
# Copy alert rules to your Prometheus rules directory
cp monitoring/prometheus/pricing_sync_alerts.yml \
   /etc/prometheus/rules/

# Reload Prometheus
curl -X POST http://your-prometheus:9090/-/reload
```

### Step 3: Import Dashboards to Grafana

1. Login to your Grafana instance
2. Go to **Dashboards → Import**
3. Upload `monitoring/grafana/pricing_sync_scheduler_health.json`
4. Upload `monitoring/grafana/pricing_sync_system_impact.json`
5. Select your Prometheus datasource
6. Click **Import**

---

## Verification Checklist

After deployment, verify everything is working:

```bash
# Run verification script
cd /path/to/gatewayz-backend
./scripts/verify_phase6_monitoring.sh
```

### Manual Verification

- [ ] **Prometheus**
  - [ ] Targets are UP (Status → Targets)
  - [ ] Metrics are being scraped (query: `pricing_scheduled_sync_runs_total`)
  - [ ] Alert rules loaded (Alerts page)
  - [ ] No errors in Prometheus logs

- [ ] **Grafana**
  - [ ] Dashboards imported successfully
  - [ ] All panels showing data (no "No Data" errors)
  - [ ] Queries executing without errors
  - [ ] Time range selector working

- [ ] **Alertmanager**
  - [ ] Configuration valid
  - [ ] Receivers configured
  - [ ] Test alert sent to Slack successfully
  - [ ] Alert routing working

- [ ] **Application**
  - [ ] Metrics endpoint accessible: `curl https://api.gatewayz.ai/metrics`
  - [ ] Pricing metrics present in output
  - [ ] Scheduler running: check `/admin/pricing/scheduler/status`

---

## Troubleshooting

### Prometheus Not Scraping

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq

# Common issues:
# 1. SSL/TLS issues - add 'insecure_skip_verify: true' under scheme
# 2. Network connectivity - test with curl first
# 3. Firewall blocking - check Railway/hosting firewall rules
```

### Dashboards Showing "No Data"

```bash
# Verify metrics exist in Prometheus
curl 'http://localhost:9090/api/v1/query?query=pricing_scheduled_sync_runs_total'

# Common issues:
# 1. Wrong datasource selected - check dashboard datasource
# 2. Time range too narrow - expand time range
# 3. Metrics not scraped yet - wait 1-2 minutes
```

### Alerts Not Firing

```bash
# Check alert rules
curl http://localhost:9090/api/v1/rules | jq

# Check Alertmanager
curl http://localhost:9093/api/v1/alerts | jq

# Common issues:
# 1. Alert threshold not met - check actual metric values
# 2. Alertmanager not connected - check Prometheus config
# 3. Evaluation interval too long - wait for evaluation cycle
```

### Slack Notifications Not Working

```bash
# Test webhook directly
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK/URL \
  -H 'Content-Type: application/json' \
  -d '{"text": "Test from Alertmanager"}'

# Common issues:
# 1. Invalid webhook URL - regenerate webhook
# 2. Wrong channel - check channel exists
# 3. Slack app permissions - reinstall app
```

---

## Production Recommendations

### Security

1. **Change default passwords**:
   ```bash
   # Grafana admin password
   docker-compose exec grafana grafana-cli admin reset-admin-password newpassword
   ```

2. **Enable authentication** for Prometheus/Alertmanager
3. **Use HTTPS** with reverse proxy (nginx/Caddy)
4. **Restrict access** with firewall rules

### High Availability

1. **Use managed services** (Grafana Cloud, AWS Managed Prometheus)
2. **Run multiple Prometheus replicas** with Thanos
3. **Configure Alertmanager clustering**
4. **Set up backup** for Grafana dashboards

### Cost Optimization

1. **Adjust scrape intervals** (30s → 60s for non-critical)
2. **Use recording rules** for expensive queries
3. **Set retention policies** (15 days for staging, 30 days for prod)
4. **Enable compression** for remote write

---

## Next Steps

1. **Monitor for 1 week** - observe alert noise and tune thresholds
2. **Train your team** - walk through dashboards and runbooks
3. **Set up on-call rotation** - configure PagerDuty if needed
4. **Document incidents** - update runbooks based on real incidents
5. **Review SLOs** - ensure alerts align with business objectives

---

## Support

**Questions?**
- Check the full setup guide: `docs/PHASE_6_MONITORING_SETUP_GUIDE.md`
- Run verification: `./scripts/verify_phase6_monitoring.sh`
- GitHub Issue: #961

**Resources**:
- Prometheus docs: https://prometheus.io/docs
- Grafana docs: https://grafana.com/docs
- Alertmanager docs: https://prometheus.io/docs/alerting/latest/alertmanager

---

**Last Updated**: 2026-01-26
**Status**: Production Ready
