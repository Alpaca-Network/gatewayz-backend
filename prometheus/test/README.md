# Prometheus/Alertmanager Test Environment

## 🎯 Purpose

This test environment fixes **3 critical issues** identified in the original Prometheus/Alertmanager setup:

### Issues Fixed

1. **❌ SMTP Environment Variables Not Substituted**
   - **Problem**: `alertmanager.yml` uses `${SMTP_USERNAME}` and `${SMTP_PASSWORD}` syntax, but the official `prom/alertmanager` image doesn't perform environment variable substitution
   - **Solution**: Custom Dockerfile with `envsubst` and entrypoint script that processes template before starting Alertmanager

2. **❌ Missing Alerting Configuration in Prometheus**
   - **Problem**: `prom.yml` was missing the `alerting` section, so Prometheus couldn't send alerts to Alertmanager
   - **Solution**: Added complete `alerting` section to `prometheus-test.yml` with proper endpoint configuration

3. **❌ Alert Rules Fail During Zero Traffic (NaN Division)**
   - **Problem**: When calculating `success_rate = successful_requests / total_requests`, during zero traffic both are 0, resulting in `NaN`. Alerts comparing `NaN < 20` always evaluate to false, so alerts don't fire during total outages
   - **Solution**: Added additional conditions to detect zero traffic and fire alerts appropriately

---

## 📁 Files

| File | Purpose |
|------|---------|
| `prometheus-test.yml` | **FIXED** Prometheus config with `alerting` section |
| `alert.rules.test.yml` | **FIXED** Alert rules that handle zero traffic/NaN |
| `alertmanager.yml.template` | Alertmanager config template (for env var substitution) |
| `Dockerfile.alertmanager` | Custom Alertmanager image with `envsubst` |
| `alertmanager-entrypoint.sh` | **FIXED** Entrypoint that substitutes env vars |
| `docker-compose.test.yml` | Test environment on different ports |
| `.env.example` | Example environment variables |
| `README.md` | This file |

---

## 🚀 Quick Start

### 1. Create `.env` file

```bash
cd prometheus/test
cp .env.example .env

# Edit .env with your Gmail credentials
SMTP_USERNAME=manjeshprasad21@gmail.com
SMTP_PASSWORD=your-16-char-app-password
```

### 2. Start Test Environment

```bash
# Build and start services
docker-compose -f docker-compose.test.yml up -d --build

# Check logs
docker-compose -f docker-compose.test.yml logs -f
```

### 3. Access Services

| Service | URL | Purpose |
|---------|-----|---------|
| **Prometheus Test** | http://localhost:9091 | View metrics & alerts |
| **Alertmanager Test** | http://localhost:9094 | View alert status |
| **Backend API** | http://localhost:8000/metrics | Metrics endpoint |

---

## 🔍 Testing the Fixes

### Test 1: Verify SMTP Env Var Substitution

```bash
# Check if environment variables were substituted correctly
docker-compose -f docker-compose.test.yml exec alertmanager-test cat /etc/alertmanager/alertmanager.yml | grep smtp_auth_username

# Should show: smtp_auth_username: 'manjeshprasad21@gmail.com'
# NOT: smtp_auth_username: '${SMTP_USERNAME}'
```

### Test 2: Verify Prometheus Can Send to Alertmanager

```bash
# Check Prometheus can reach Alertmanager
curl http://localhost:9091/api/v1/alertmanagers | jq

# Should show alertmanager endpoint as "up"
```

### Test 3: Verify Alerts Handle Zero Traffic

```bash
# Query current traffic
curl 'http://localhost:9091/api/v1/query?query=sum(rate(model_inference_requests_total[10m]))'

# If result is 0, check if NoTrafficDetected alert fires
curl 'http://localhost:9091/api/v1/alerts' | jq '.data.alerts[] | select(.labels.alertname=="NoTrafficDetected")'
```

---

## 🧪 Test Scenarios

### Scenario 1: Normal Operation (Success Rate 80%)

**Expected**: No alerts fire

```bash
# Generate some successful requests
for i in {1..100}; do curl http://localhost:8000/metrics; done

# Wait 5-10 minutes
# Check Prometheus alerts
open http://localhost:9091/alerts
```

### Scenario 2: High Error Rate (Success Rate 15%)

**Expected**: `LowModelHealthScore` alert fires after 5 minutes

```bash
# Simulate errors (if you have a test endpoint)
# Or manually check existing metrics

# Wait 5+ minutes
# Check Alertmanager for firing alerts
open http://localhost:9094/#/alerts
```

### Scenario 3: Zero Traffic (Total Outage)

**Expected**: `NoTrafficDetected` alert fires after 10 minutes

```bash
# Stop making requests to backend
# Wait 10+ minutes

# Check alert status
curl 'http://localhost:9091/api/v1/alerts' | jq '.data.alerts[] | select(.labels.alertname=="NoTrafficDetected")'
```

### Scenario 4: Email Alert Delivery

**Expected**: Email sent to manjeshprasad21@gmail.com

```bash
# Force an alert by editing threshold
# Edit alert.rules.test.yml: Change `< 20` to `< 99`

# Reload Prometheus config
curl -X POST http://localhost:9091/-/reload

# Wait 5-10 minutes
# Check email inbox
```

---

## 📊 How the Fixes Work

### Fix 1: Environment Variable Substitution

**Before** (Original):
```yaml
# alertmanager.yml
smtp_auth_username: '${SMTP_USERNAME}'  # ❌ Not substituted
```

**After** (Fixed):
```bash
# alertmanager-entrypoint.sh
envsubst < /etc/alertmanager/alertmanager.yml.template > /etc/alertmanager/alertmanager.yml
# Now: smtp_auth_username: 'manjeshprasad21@gmail.com'  ✅
```

### Fix 2: Prometheus Alerting Configuration

**Before** (Missing):
```yaml
# prometheus.yml
# alerting:  # ❌ Missing section
```

**After** (Fixed):
```yaml
# prometheus-test.yml
alerting:  # ✅ Added
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']
```

### Fix 3: Zero Traffic / NaN Handling

**Before** (Broken):
```yaml
# Alert only checks ratio
expr: |
  (
    sum(rate(model_inference_requests_total{status="success"}[10m]))
    /
    sum(rate(model_inference_requests_total[10m]))
  ) * 100 < 20  
# ❌ When both are 0, this is NaN, alert doesn't fire
```

**After** (Fixed):
```yaml
# Alert checks ratio OR zero traffic
expr: |
  (
    # Check success rate
    (sum(rate(...{status="success"}...)) / sum(rate(...))) * 100 < 20
  )
  or
  (
    # Fire if zero traffic (outage)
    sum(rate(model_inference_requests_total[10m])) == 0  # ✅
  )
```

---

## 🛠️ Troubleshooting

### Issue: Alertmanager shows SMTP auth failed

```bash
# Check if env vars were substituted
docker-compose -f docker-compose.test.yml logs alertmanager-test | grep SMTP

# Verify .env file exists and has correct values
cat .env

# Rebuild container
docker-compose -f docker-compose.test.yml up -d --build alertmanager-test
```

### Issue: Prometheus can't reach Alertmanager

```bash
# Check if alertmanager is running
docker-compose -f docker-compose.test.yml ps alertmanager-test

# Check network connectivity
docker-compose -f docker-compose.test.yml exec prometheus-test wget -qO- http://alertmanager-test:9093/-/healthy

# Check Prometheus config
curl 'http://localhost:9091/api/v1/status/config' | jq '.data.yaml' | grep -A5 alerting
```

### Issue: Alerts not firing during zero traffic

```bash
# Verify alert rules are loaded
curl 'http://localhost:9091/api/v1/rules' | jq '.data.groups[] | select(.name=="model_health_alerts_test")'

# Check if NoTrafficDetected rule exists
curl 'http://localhost:9091/api/v1/rules' | jq '.data.groups[].rules[] | select(.name=="NoTrafficDetected")'

# Manually evaluate expression
curl 'http://localhost:9091/api/v1/query?query=sum(rate(model_inference_requests_total[15m]))==0'
```

---

## 📖 URLs Without Conflicts

All services run on different ports to avoid conflicts:

| Service | Original Port | Test Port | URL |
|---------|---------------|-----------|-----|
| Prometheus | 9090 | **9091** | http://localhost:9091 |
| Alertmanager | 9093 | **9094** | http://localhost:9094 |
| Backend API | 8000 | 8000 | http://localhost:8000 |

---

## 🎯 Next Steps

### After Testing Successfully

1. **Apply fixes to production**:
   - Copy fixed files to `railway-grafana-stack/prometheus/`
   - Update `docker-compose.yml` to use custom Alertmanager image
   - Deploy to Railway

2. **Set production env vars**:
   ```
   SMTP_USERNAME = manjeshprasad21@gmail.com
   SMTP_PASSWORD = your-app-password
   ```

3. **Monitor alerts**:
   - Check Prometheus: http://your-prometheus.railway.app/alerts
   - Check Alertmanager: http://your-alertmanager.railway.app

---

## 📝 Summary of Changes

### Configuration Files

| Original | Fixed | Changes |
|----------|-------|---------|
| `prom.yml` | `prometheus-test.yml` | ✅ Added `alerting` section |
| `alert.rules.yml` | `alert.rules.test.yml` | ✅ Handle zero traffic/NaN |
| `alertmanager.yml` | `alertmanager.yml.template` | ✅ Use as template |
| N/A | `alertmanager-entrypoint.sh` | ✅ New: env var substitution |
| N/A | `Dockerfile.alertmanager` | ✅ New: custom image with envsubst |

### Test Environment

- ✅ Runs on ports 9091 (Prometheus) and 9094 (Alertmanager)
- ✅ No conflicts with existing services
- ✅ Isolated Docker network
- ✅ Separate data volumes

---

**Status**: Ready for testing  
**Email**: manjeshprasad21@gmail.com  
**Test Ports**: 9091 (Prometheus), 9094 (Alertmanager)
