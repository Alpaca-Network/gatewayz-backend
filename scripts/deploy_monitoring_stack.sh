#!/bin/bash
# One-command deployment script for Phase 6 monitoring stack
# This sets up Prometheus + Grafana + Alertmanager with Docker

set -e

echo "🚀 Phase 6 Monitoring Stack - Quick Deploy"
echo "=========================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install it first:"
    echo "   https://docs.docker.com/compose/install/"
    exit 1
fi

# Ask for deployment directory
read -p "📁 Where to install monitoring stack? [~/gatewayz-monitoring]: " DEPLOY_DIR
DEPLOY_DIR=${DEPLOY_DIR:-~/gatewayz-monitoring}
DEPLOY_DIR=$(eval echo "$DEPLOY_DIR")  # Expand ~

echo "✅ Will deploy to: $DEPLOY_DIR"

# Ask for Slack webhook (optional)
read -p "🔔 Slack webhook URL (optional, press enter to skip): " SLACK_WEBHOOK

# Create directory structure
echo ""
echo "📦 Creating directory structure..."
mkdir -p "$DEPLOY_DIR"/{prometheus/rules,grafana/provisioning/{datasources,dashboards},alertmanager}
cd "$DEPLOY_DIR"

# Get the repository path
REPO_PATH=$(cd "$(dirname "$0")/.." && pwd)
echo "📂 Repository path: $REPO_PATH"

# Copy alert rules
echo "📋 Copying alert rules..."
cp "$REPO_PATH/monitoring/prometheus/pricing_sync_alerts.yml" prometheus/rules/

# Copy dashboards
echo "📊 Copying Grafana dashboards..."
cp "$REPO_PATH/monitoring/grafana/"*.json grafana/provisioning/dashboards/

# Create docker-compose.yml
echo "🐳 Creating Docker Compose configuration..."
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
      - '--storage.tsdb.retention.time=30d'
    restart: unless-stopped
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    container_name: gatewayz-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
      - GF_SERVER_ROOT_URL=http://localhost:3000
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    restart: unless-stopped
    depends_on:
      - prometheus
    networks:
      - monitoring

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
    networks:
      - monitoring

volumes:
  prometheus-data:
  grafana-data:
  alertmanager-data:

networks:
  monitoring:
    driver: bridge
EOF

# Create Prometheus configuration
echo "⚙️  Creating Prometheus configuration..."
cat > prometheus/prometheus.yml << 'EOF'
global:
  scrape_interval: 30s
  evaluation_interval: 30s
  external_labels:
    cluster: 'gatewayz'
    environment: 'production'

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

rule_files:
  - "rules/*.yml"

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

  # Self-monitoring
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'grafana'
    static_configs:
      - targets: ['grafana:3000']

  - job_name: 'alertmanager'
    static_configs:
      - targets: ['alertmanager:9093']
EOF

# Create Alertmanager configuration
echo "📢 Creating Alertmanager configuration..."
if [ -n "$SLACK_WEBHOOK" ]; then
  SLACK_CONFIG="
      - api_url: '$SLACK_WEBHOOK'
        channel: '#platform-critical'
        title: '🚨 CRITICAL: {{ .GroupLabels.alertname }}'
        text: |
          *Severity*: {{ .CommonLabels.severity }}
          *Description*: {{ .CommonAnnotations.description }}
          *Action*: {{ .CommonAnnotations.action }}
        send_resolved: true"
else
  SLACK_CONFIG="
      # Add your Slack webhook here
      # - api_url: 'YOUR_SLACK_WEBHOOK_URL'
      #   channel: '#platform-critical'"
fi

cat > alertmanager/alertmanager.yml << EOF
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'component']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    - match:
        severity: critical
        component: pricing_sync
      receiver: 'slack-critical'

    - match:
        severity: warning
        component: pricing_sync
      receiver: 'slack-warnings'

receivers:
  - name: 'default'
    slack_configs:$SLACK_CONFIG

  - name: 'slack-critical'
    slack_configs:$SLACK_CONFIG

  - name: 'slack-warnings'
    slack_configs:$SLACK_CONFIG

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'component']
EOF

# Create Grafana datasource provisioning
echo "🔌 Creating Grafana datasource configuration..."
cat > grafana/provisioning/datasources/prometheus.yml << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: 30s
EOF

# Create Grafana dashboard provisioning
echo "📈 Creating Grafana dashboard configuration..."
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

# Start the stack
echo ""
echo "🚀 Starting monitoring stack..."
docker-compose up -d

# Wait for services to be healthy
echo ""
echo "⏳ Waiting for services to start..."
sleep 5

# Check health
echo ""
echo "🏥 Checking service health..."

PROMETHEUS_HEALTHY=false
GRAFANA_HEALTHY=false
ALERTMANAGER_HEALTHY=false

for i in {1..30}; do
    if curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
        PROMETHEUS_HEALTHY=true
    fi

    if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
        GRAFANA_HEALTHY=true
    fi

    if curl -s http://localhost:9093/-/healthy > /dev/null 2>&1; then
        ALERTMANAGER_HEALTHY=true
    fi

    if $PROMETHEUS_HEALTHY && $GRAFANA_HEALTHY && $ALERTMANAGER_HEALTHY; then
        break
    fi

    sleep 1
done

echo ""
echo "=========================================="
echo "✅ Deployment Complete!"
echo "=========================================="
echo ""

if $PROMETHEUS_HEALTHY; then
    echo "✅ Prometheus:    http://localhost:9090"
else
    echo "⚠️  Prometheus:    Starting... (check logs with: docker-compose logs prometheus)"
fi

if $GRAFANA_HEALTHY; then
    echo "✅ Grafana:       http://localhost:3000"
    echo "   └─ Login:      admin / admin"
else
    echo "⚠️  Grafana:       Starting... (check logs with: docker-compose logs grafana)"
fi

if $ALERTMANAGER_HEALTHY; then
    echo "✅ Alertmanager:  http://localhost:9093"
else
    echo "⚠️  Alertmanager:  Starting... (check logs with: docker-compose logs alertmanager)"
fi

echo ""
echo "📚 Next Steps:"
echo "   1. Visit Grafana at http://localhost:3000"
echo "   2. Login with admin/admin (change password on first login)"
echo "   3. Navigate to Dashboards → Pricing Sync Monitoring"
echo "   4. Check Prometheus targets: http://localhost:9090/targets"
echo "   5. View alerts: http://localhost:9090/alerts"
echo ""

if [ -z "$SLACK_WEBHOOK" ]; then
    echo "⚠️  Slack notifications not configured"
    echo "   To add later, edit: $DEPLOY_DIR/alertmanager/alertmanager.yml"
    echo "   Then restart: docker-compose restart alertmanager"
    echo ""
fi

echo "📖 Documentation:"
echo "   Quick Guide:  $REPO_PATH/docs/PHASE_6_QUICK_DEPLOY.md"
echo "   Full Guide:   $REPO_PATH/docs/PHASE_6_MONITORING_SETUP_GUIDE.md"
echo "   Runbooks:     $REPO_PATH/docs/runbooks/"
echo ""

echo "🔧 Useful Commands:"
echo "   View logs:         docker-compose logs -f"
echo "   Stop stack:        docker-compose down"
echo "   Restart services:  docker-compose restart"
echo "   Update dashboards: Copy new JSON files and run 'docker-compose restart grafana'"
echo ""

echo "🎉 Happy monitoring!"
