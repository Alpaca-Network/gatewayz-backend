# Downtime Monitoring System

## Overview

The Downtime Monitoring System automatically tracks and logs application downtime incidents for the Gatewayz API. When the `/health` endpoint fails, the system:

1. **Detects** downtime by checking the health endpoint every minute
2. **Creates** incident records in the database
3. **Captures** logs from 5 minutes before to 5 minutes after the downtime
4. **Stores** logs for debugging and post-mortem analysis
5. **Resolves** incidents when the service recovers

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Health Monitor Script (scripts/monitoring/health_monitor.py) │
│                                                               │
│  - Runs independently (separate process/container)           │
│  - Checks /health endpoint every 60 seconds                  │
│  - Detects failures and recoveries                           │
└────────────────┬─────────────────────────────────────────────┘
                 │
                 ├─ Health Check Fails
                 │
                 ▼
┌────────────────────────────────────────────────────────────────┐
│  Downtime Incident Database (downtime_incidents table)         │
│                                                                │
│  - incident_id (UUID)                                          │
│  - started_at, ended_at, duration_seconds                     │
│  - error_message, http_status_code, response_body             │
│  - status (ongoing, resolved, investigating)                  │
│  - severity (low, medium, high, critical)                     │
│  - logs_captured (JSONB array) or logs_file_path (string)     │
│  - metrics_snapshot (JSONB)                                   │
└────────────────┬───────────────────────────────────────────────┘
                 │
                 ├─ Capture Logs
                 │
                 ▼
┌────────────────────────────────────────────────────────────────┐
│  Log Capture Service (src/services/downtime_log_capture.py)    │
│                                                                │
│  - Queries Grafana Loki for logs                              │
│  - Time range: 5 min before → 5 min after downtime            │
│  - Stores in database (JSONB) or files (JSON)                 │
│  - Supports filtering and analysis                            │
└────────────────┬───────────────────────────────────────────────┘
                 │
                 ├─ View & Analyze
                 │
                 ▼
┌────────────────────────────────────────────────────────────────┐
│  Admin API Routes (src/routes/downtime_logs.py)                │
│                                                                │
│  GET  /admin/downtime/incidents                               │
│  GET  /admin/downtime/incidents/ongoing                       │
│  GET  /admin/downtime/incidents/{id}                          │
│  GET  /admin/downtime/incidents/{id}/logs                     │
│  GET  /admin/downtime/incidents/{id}/analysis                 │
│  POST /admin/downtime/incidents/{id}/capture-logs             │
│  POST /admin/downtime/incidents/{id}/resolve                  │
│  GET  /admin/downtime/statistics                              │
└────────────────────────────────────────────────────────────────┘
```

## Components

### 1. Database Migration

**File:** `supabase/migrations/20260212000000_create_downtime_incidents_table.sql`

Creates the `downtime_incidents` table with:
- Incident timing (started_at, detected_at, ended_at, duration_seconds)
- Health check details (health_endpoint, error_message, http_status_code)
- Status and severity tracking
- Log storage (JSONB array or file path)
- Metadata (environment, server_info, metrics_snapshot)
- Indexes for efficient querying

**Apply migration:**
```bash
supabase db push
# or
supabase migration up
```

### 2. Database Module

**File:** `src/db/downtime_incidents.py`

Functions:
- `create_incident()` - Create new incident record
- `update_incident()` - Update incident (add logs, set end time, etc.)
- `get_incident(incident_id)` - Get specific incident
- `get_ongoing_incidents()` - Get all ongoing incidents
- `get_recent_incidents()` - Get recent incidents with filters
- `get_incidents_by_date_range()` - Get incidents in date range
- `resolve_incident()` - Mark incident as resolved
- `get_incident_statistics()` - Get downtime statistics
- `cleanup_old_incidents()` - Clean up old incidents

### 3. Log Capture Service

**File:** `src/services/downtime_log_capture.py`

Features:
- **Loki Integration**: Queries Grafana Loki for logs
- **Time Range**: 5 minutes before → 5 minutes after downtime
- **Storage Options**: Database (JSONB) or files (JSON)
- **Filtering**: Filter logs by level, logger, search term
- **Analysis**: Analyze logs for error patterns

Functions:
- `query_loki_logs()` - Query logs from Loki
- `capture_downtime_logs()` - Main log capture function
- `capture_logs_for_ongoing_incident()` - Capture for ongoing incident
- `capture_logs_for_resolved_incident()` - Capture for resolved incident
- `get_filtered_logs()` - Filter captured logs
- `analyze_logs_for_errors()` - Analyze error patterns

### 4. Health Monitor Script

**File:** `scripts/monitoring/health_monitor.py`

**Runs independently as:**
- Standalone Python script
- Systemd service
- Docker container
- Kubernetes pod

**Features:**
- Checks `/health` endpoint every 60 seconds (configurable)
- Detects downtime (consecutive failures)
- Creates incident records automatically
- Captures logs on resolution
- Handles graceful shutdown
- Metrics export (optional)

**Usage:**
```bash
# Run locally
python scripts/monitoring/health_monitor.py \
  --url https://api.gatewayz.ai \
  --interval 60 \
  --timeout 10

# Run with custom settings
python scripts/monitoring/health_monitor.py \
  --url http://localhost:8000 \
  --interval 30 \
  --no-log-capture \
  --verbose

# Run as systemd service
sudo systemctl start gatewayz-health-monitor.service
```

**Environment Variables:**
```bash
GATEWAYZ_URL=https://api.gatewayz.ai
HEALTH_CHECK_INTERVAL=60
HEALTH_CHECK_TIMEOUT=10
LOKI_ENABLED=true
LOKI_QUERY_URL=http://loki:3100
GRAFANA_LOKI_USERNAME=<username>
GRAFANA_LOKI_API_KEY=<api_key>
```

### 5. Admin API Routes

**File:** `src/routes/downtime_logs.py`

All routes require **admin authentication**.

#### List Incidents
```bash
GET /admin/downtime/incidents
Query params:
  - limit: int (1-500, default: 50)
  - status: "ongoing" | "resolved" | "investigating"
  - severity: "low" | "medium" | "high" | "critical"
  - environment: string
```

#### Get Ongoing Incidents
```bash
GET /admin/downtime/incidents/ongoing
```

#### Get Specific Incident
```bash
GET /admin/downtime/incidents/{incident_id}
```

#### Get Incident Logs
```bash
GET /admin/downtime/incidents/{incident_id}/logs
Query params:
  - level: "ERROR" | "WARNING" | "INFO" | "DEBUG"
  - logger_name: string
  - search: string
```

#### Analyze Incident Logs
```bash
GET /admin/downtime/incidents/{incident_id}/analysis
```

Returns:
```json
{
  "status": "success",
  "incident_id": "uuid",
  "analysis": {
    "total_logs": 1234,
    "error_count": 45,
    "warning_count": 12,
    "error_types": {
      "ConnectionError": 20,
      "TimeoutError": 15,
      "ValueError": 10
    },
    "top_errors": [
      ["Database connection timeout", 15],
      ["Redis connection failed", 10]
    ]
  }
}
```

#### Manually Capture Logs
```bash
POST /admin/downtime/incidents/{incident_id}/capture-logs
```

#### Resolve Incident
```bash
POST /admin/downtime/incidents/{incident_id}/resolve
Query params:
  - notes: string (optional)
```

#### Get Statistics
```bash
GET /admin/downtime/statistics
Query params:
  - days: int (1-365, default: 30)
```

Returns:
```json
{
  "status": "success",
  "period_days": 30,
  "statistics": {
    "total_incidents": 5,
    "total_downtime_seconds": 450,
    "average_duration_seconds": 90,
    "by_severity": {
      "critical": 2,
      "high": 2,
      "medium": 1
    },
    "by_status": {
      "resolved": 4,
      "ongoing": 1
    }
  }
}
```

## Deployment

### Option 1: Systemd Service (Recommended for VPS/Dedicated Server)

Create `/etc/systemd/system/gatewayz-health-monitor.service`:

```ini
[Unit]
Description=Gatewayz Health Monitor
After=network.target

[Service]
Type=simple
User=gatewayz
WorkingDirectory=/opt/gatewayz/backend
ExecStart=/opt/gatewayz/backend/venv/bin/python scripts/monitoring/health_monitor.py --url https://api.gatewayz.ai
Restart=always
RestartSec=10

# Environment variables
Environment="LOKI_ENABLED=true"
Environment="LOKI_QUERY_URL=http://loki:3100"
Environment="HEALTH_CHECK_INTERVAL=60"

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable gatewayz-health-monitor.service
sudo systemctl start gatewayz-health-monitor.service
sudo systemctl status gatewayz-health-monitor.service
```

View logs:
```bash
sudo journalctl -u gatewayz-health-monitor.service -f
```

### Option 2: Docker Container

Create `docker-compose.health-monitor.yml`:

```yaml
version: '3.8'

services:
  health-monitor:
    build:
      context: .
      dockerfile: Dockerfile.health-monitor
    container_name: gatewayz-health-monitor
    restart: always
    environment:
      - GATEWAYZ_URL=https://api.gatewayz.ai
      - HEALTH_CHECK_INTERVAL=60
      - HEALTH_CHECK_TIMEOUT=10
      - LOKI_ENABLED=true
      - LOKI_QUERY_URL=http://loki:3100
      - GRAFANA_LOKI_USERNAME=${GRAFANA_LOKI_USERNAME}
      - GRAFANA_LOKI_API_KEY=${GRAFANA_LOKI_API_KEY}
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_KEY=${SUPABASE_KEY}
    networks:
      - monitoring
    volumes:
      - ./logs:/app/logs
      - ./logs/downtime:/app/logs/downtime

networks:
  monitoring:
    external: true
```

Create `Dockerfile.health-monitor`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY scripts/ ./scripts/

# Create logs directory
RUN mkdir -p /app/logs /app/logs/downtime

# Run health monitor
CMD ["python", "scripts/monitoring/health_monitor.py"]
```

Run:
```bash
docker-compose -f docker-compose.health-monitor.yml up -d
```

### Option 3: Kubernetes Deployment

Create `k8s/health-monitor-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gatewayz-health-monitor
  namespace: gatewayz
spec:
  replicas: 1
  selector:
    matchLabels:
      app: health-monitor
  template:
    metadata:
      labels:
        app: health-monitor
    spec:
      containers:
      - name: health-monitor
        image: gatewayz/health-monitor:latest
        env:
        - name: GATEWAYZ_URL
          value: "https://api.gatewayz.ai"
        - name: HEALTH_CHECK_INTERVAL
          value: "60"
        - name: LOKI_ENABLED
          value: "true"
        - name: LOKI_QUERY_URL
          valueFrom:
            configMapKeyRef:
              name: gatewayz-config
              key: loki-query-url
        - name: SUPABASE_URL
          valueFrom:
            secretKeyRef:
              name: gatewayz-secrets
              key: supabase-url
        - name: SUPABASE_KEY
          valueFrom:
            secretKeyRef:
              name: gatewayz-secrets
              key: supabase-key
        volumeMounts:
        - name: logs
          mountPath: /app/logs
      volumes:
      - name: logs
        persistentVolumeClaim:
          claimName: health-monitor-logs
```

Apply:
```bash
kubectl apply -f k8s/health-monitor-deployment.yaml
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GATEWAYZ_URL` | No | `https://api.gatewayz.ai` | Base URL of API to monitor |
| `HEALTH_CHECK_INTERVAL` | No | `60` | Seconds between health checks |
| `HEALTH_CHECK_TIMEOUT` | No | `10` | HTTP request timeout in seconds |
| `LOKI_ENABLED` | No | `false` | Enable Loki log querying |
| `LOKI_QUERY_URL` | Yes (if Loki enabled) | - | Loki query endpoint URL |
| `GRAFANA_LOKI_USERNAME` | No | - | Grafana Cloud username |
| `GRAFANA_LOKI_API_KEY` | No | - | Grafana Cloud API key |
| `SUPABASE_URL` | Yes | - | Supabase project URL |
| `SUPABASE_KEY` | Yes | - | Supabase service role key |

### Health Monitor CLI Options

```
--url URL                  Base URL to monitor (default: env GATEWAYZ_URL)
--interval SECONDS         Check interval (default: 60)
--timeout SECONDS          HTTP timeout (default: 10)
--no-log-capture           Disable automatic log capture
--enable-notifications     Enable email notifications
--verbose, -v              Enable verbose logging (DEBUG level)
```

## Usage Examples

### 1. View Recent Downtime Incidents

```bash
curl -X GET "https://api.gatewayz.ai/admin/downtime/incidents?limit=10" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}"
```

### 2. Get Ongoing Incidents

```bash
curl -X GET "https://api.gatewayz.ai/admin/downtime/incidents/ongoing" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}"
```

### 3. View Logs for an Incident

```bash
curl -X GET "https://api.gatewayz.ai/admin/downtime/incidents/${INCIDENT_ID}/logs?level=ERROR" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}"
```

### 4. Analyze Incident Logs

```bash
curl -X GET "https://api.gatewayz.ai/admin/downtime/incidents/${INCIDENT_ID}/analysis" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}"
```

### 5. Get Downtime Statistics

```bash
curl -X GET "https://api.gatewayz.ai/admin/downtime/statistics?days=30" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}"
```

## Troubleshooting

### Health Monitor Not Detecting Downtime

**Check:**
1. Monitor is running: `systemctl status gatewayz-health-monitor`
2. Network connectivity to API: `curl https://api.gatewayz.ai/health`
3. Database connection: Check logs for Supabase errors
4. Monitor logs: `journalctl -u gatewayz-health-monitor -f`

### Logs Not Being Captured

**Check:**
1. `LOKI_ENABLED=true` in environment
2. `LOKI_QUERY_URL` is correct
3. Loki authentication (if using Grafana Cloud)
4. Loki query endpoint is accessible
5. Logs exist in Loki for the time range

**Test Loki connection:**
```bash
curl -G "http://loki:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={app="gatewayz-api"}' \
  --data-urlencode "start=$(date -u -d '5 minutes ago' '+%s')000000000" \
  --data-urlencode "end=$(date -u '+%s')000000000"
```

### Database Migration Failed

**Check:**
1. Supabase connection: `supabase status`
2. Migration file syntax
3. Existing table conflicts: `DROP TABLE IF EXISTS downtime_incidents;`
4. PostgREST schema cache: `NOTIFY pgrst, 'reload schema';`

### API Routes Not Loading

**Check:**
1. Route registered in `src/main.py`: Look for `"downtime_logs"`
2. Import errors: Check application startup logs
3. Dependencies installed: `pip install -r requirements.txt`

## Best Practices

### 1. Log Retention

Configure log retention based on your needs:

```python
# In health monitor or cron job
from src.db.downtime_incidents import cleanup_old_incidents

# Keep incidents for 90 days, preserve critical
cleanup_old_incidents(days=90, keep_critical=True)
```

### 2. Alert Notifications

Extend the health monitor to send alerts:

```python
# In health_monitor.py
def _send_downtime_notification(self, incident: dict[str, Any]) -> None:
    # Send email via Resend
    # Send Slack notification
    # Send PagerDuty alert
    pass
```

### 3. Metrics Dashboards

Create Grafana dashboards using the statistics endpoint:

```sql
-- Prometheus query
gatewayz_downtime_incidents_total
gatewayz_downtime_duration_seconds_total
gatewayz_downtime_incidents_by_severity{severity="critical"}
```

### 4. Post-Mortem Analysis

For each incident:
1. View incident details
2. Analyze captured logs
3. Check error patterns
4. Review metrics snapshot
5. Document root cause
6. Update incident notes

## Security

- **Admin-Only Access**: All API routes require admin authentication
- **Encrypted Storage**: Logs stored in database are encrypted at rest
- **Rate Limiting**: API endpoints are rate-limited
- **Audit Logging**: All incident operations are logged
- **IP Allowlisting**: Restrict health monitor IP if needed

## Performance

- **Log Capture Limits**: Maximum 10,000 logs per incident
- **Storage Options**: Use files for large log sets (>1000 logs)
- **Database Indexes**: Optimized queries with indexes
- **Async Operations**: Log capture runs asynchronously
- **Caching**: Health monitor uses in-memory caching

## Support

For issues or questions:
- Check logs: `journalctl -u gatewayz-health-monitor -f`
- View incidents: `GET /admin/downtime/incidents`
- Test health endpoint: `curl https://api.gatewayz.ai/health`
- Review documentation: `docs/DOWNTIME_MONITORING.md`

## Future Enhancements

- [ ] Email/Slack notifications
- [ ] Prometheus metrics export
- [ ] Automated root cause analysis
- [ ] Incident playbooks
- [ ] Correlation with other metrics
- [ ] SLA tracking
- [ ] Uptime percentage calculations
