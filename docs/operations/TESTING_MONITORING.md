# Testing the Monitoring Stack

## Quick Start

### 1. Start the Server

```bash
# Terminal 1 - Start the FastAPI server
uvicorn src.main:app --reload
```

### 2. Install Test Dependencies

```bash
pip install colorama requests
```

### 3. Run the Comprehensive Test Suite

```bash
# Basic test (shows pass/fail for each component)
python scripts/test_monitoring_stack.py

# Verbose mode (shows detailed output)
python scripts/test_monitoring_stack.py --verbose

# Skip database tests
python scripts/test_monitoring_stack.py --skip-db

# Skip Redis tests
python scripts/test_monitoring_stack.py --skip-redis
```

---

## What Gets Tested

The test script validates **11 major components**:

### 1. ✅ Server Health Check
- Verifies server is running
- Tests `/health` endpoint

### 2. ✅ Prometheus Metrics
- Tests `/metrics` endpoint
- Verifies 8 core metrics are exposed:
  - `model_inference_requests_total`
  - `model_inference_duration_seconds`
  - `tokens_used_total`
  - `credits_used_total`
  - `database_query_total`
  - `database_query_duration_seconds`
  - `http_requests_total`
  - `http_request_duration_seconds`

### 3. ✅ Monitoring API (16 endpoints)
- `/api/monitoring/health` - Provider health scores
- `/api/monitoring/stats/realtime` - Real-time statistics
- `/api/monitoring/circuit-breakers` - Circuit breaker states
- `/api/monitoring/providers/comparison` - Provider comparison
- `/api/monitoring/anomalies` - Anomaly detection
- `/api/monitoring/trial-analytics` - Trial funnel
- `/api/monitoring/cost-analysis` - Cost breakdown
- `/api/monitoring/error-rates` - Error rates by model

### 4. ✅ Redis Metrics Service
- Tests Redis connection
- Tests metrics recording
- Tests health score retrieval

### 5. ✅ Database Schema
- Verifies `metrics_hourly_aggregates` table exists
- Verifies `provider_stats_24h` materialized view exists

### 6. ✅ Analytics Service
- Tests trial analytics function
- Tests provider comparison
- Tests anomaly detection

### 7. ✅ Circuit Breakers
- Verifies circuit breaker service initialization
- Tests model availability checking

### 8. ✅ Health Monitoring
- Tests active health monitoring
- Tests passive health capture

### 9. ✅ Metrics Aggregator
- Verifies aggregator initialization
- Checks aggregation methods

### 10. ✅ Configuration
- Tests Grafana Cloud config variables
- Tests Redis config variables
- Tests metrics aggregation config

### 11. ✅ Sentry Configuration
- Verifies adaptive sampling configuration

---

## Expected Output

### ✅ All Tests Passing

```
======================================================================
Gatewayz Monitoring Stack - Comprehensive Test Suite
======================================================================

Testing against: http://localhost:8000
Verbose mode: False
Skip database: False
Skip Redis: False

======================================================================
1. Server Health Check
======================================================================
✓ Server is running

======================================================================
2. Prometheus Metrics
======================================================================
✓ Prometheus /metrics endpoint accessible
✓ Metric 'model_inference_requests_total' present
✓ Metric 'tokens_used_total' present
...

======================================================================
Test Summary
======================================================================
Total:    45
Passed:   45
Failed:   0
Warnings: 0
Skipped:  0

======================================================================
🎉 All tests passed!
======================================================================
```

### ⚠ Tests with Warnings

```
======================================================================
Test Summary
======================================================================
Total:    45
Passed:   40
Failed:   0
Warnings: 5
Skipped:  0

======================================================================
⚠ Tests passed with warnings
======================================================================
```

**Common warnings:**
- Metrics not present yet (no data collected)
- Redis disabled in config
- Active health monitoring not running

### ❌ Tests Failing

```
======================================================================
Test Summary
======================================================================
Total:    45
Passed:   35
Failed:   5
Warnings: 2
Skipped:  3

Failed Tests:
  • Table 'metrics_hourly_aggregates'
    Table not found - run migration!

======================================================================
❌ Some tests failed
======================================================================
```

---

## Troubleshooting

### Server Not Running

**Error:**
```
✗ Server health check
  Error: Cannot connect to server. Is it running?
```

**Fix:**
```bash
uvicorn src.main:app --reload
```

### Database Migration Not Run

**Error:**
```
✗ Table 'metrics_hourly_aggregates'
  Table not found - run migration!
```

**Fix:**
```bash
supabase migration up
# or
psql $DATABASE_URL -f supabase/migrations/20251127000000_add_metrics_hourly_aggregates.sql
```

### Redis Not Running

**Error:**
```
✗ Redis connection test
  Error: Connection refused
```

**Fix:**
```bash
# Docker
docker run -d -p 6379:6379 redis:latest

# macOS (Homebrew)
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis
```

### Metrics Not Present Yet

**Warning:**
```
⚠ Metric 'model_inference_requests_total' present
  Warning: Metric not found (may not have data yet)
```

**Fix:**
Make some inference requests to generate metrics:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Then re-run the test script.

---

## Manual Testing

### Test Individual Endpoints

```bash
# 1. Test Prometheus metrics
curl http://localhost:8000/metrics | grep model_inference

# 2. Test provider health
curl http://localhost:8000/api/monitoring/health | jq

# 3. Test real-time stats
curl http://localhost:8000/api/monitoring/stats/realtime | jq

# 4. Test circuit breakers
curl http://localhost:8000/api/monitoring/circuit-breakers | jq

# 5. Test provider comparison
curl http://localhost:8000/api/monitoring/providers/comparison | jq

# 6. Test anomalies
curl http://localhost:8000/api/monitoring/anomalies | jq

# 7. Test trial analytics
curl http://localhost:8000/api/monitoring/trial-analytics | jq

# 8. Test cost analysis
curl http://localhost:8000/api/monitoring/cost-analysis?days=7 | jq
```

### Test Redis Metrics

```python
python3 << 'EOF'
import asyncio
from src.services.redis_metrics import get_redis_metrics

async def test():
    redis = get_redis_metrics()

    # Record a test request
    await redis.record_request(
        provider="openrouter",
        model="gpt-4",
        latency_ms=500,
        success=True,
        cost=0.05,
        tokens_input=100,
        tokens_output=50
    )

    # Get health score
    score = await redis.get_provider_health("openrouter")
    print(f"Health score: {score}")

    # Get recent errors
    errors = await redis.get_recent_errors("openrouter", limit=10)
    print(f"Recent errors: {len(errors)}")

asyncio.run(test())
EOF
```

### Test Database

```sql
-- Connect to database
psql $DATABASE_URL

-- Check metrics table
SELECT * FROM metrics_hourly_aggregates
ORDER BY hour DESC
LIMIT 10;

-- Check materialized view
SELECT * FROM provider_stats_24h;

-- Check table schema
\d metrics_hourly_aggregates
```

### Test Metrics Aggregation

```bash
# Run aggregation manually
python -m src.services.metrics_aggregator

# Run with verbose output
python -m src.services.metrics_aggregator --verbose

# Run periodic (stays running)
python -m src.services.metrics_aggregator --periodic
```

---

## Automated Testing with pytest

Run the full test suite:

```bash
# Run all monitoring tests
pytest tests/routes/test_monitoring.py -v
pytest tests/services/test_redis_metrics.py -v

# Run specific test class
pytest tests/routes/test_monitoring.py::TestHealthEndpoints -v

# Run with coverage
pytest tests/routes/test_monitoring.py --cov=src.routes.monitoring
pytest tests/services/test_redis_metrics.py --cov=src.services.redis_metrics

# Run all tests (entire project)
pytest tests/ -v
```

---

## Load Testing (Optional)

Generate traffic to test monitoring under load:

```bash
# Install dependencies
pip install locust

# Create locustfile.py (example)
cat > locustfile.py << 'EOF'
from locust import HttpUser, task, between

class MonitoringUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def get_health(self):
        self.client.get("/api/monitoring/health")

    @task(2)
    def get_stats(self):
        self.client.get("/api/monitoring/stats/realtime")

    @task(1)
    def get_metrics(self):
        self.client.get("/metrics")
EOF

# Run load test
locust -f locustfile.py --host=http://localhost:8000 --users 10 --spawn-rate 2
```

Then open http://localhost:8089 to view load test results.

---

## CI/CD Integration

Add to your GitHub Actions workflow:

```yaml
# .github/workflows/test-monitoring.yml
name: Test Monitoring Stack

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      redis:
        image: redis:latest
        ports:
          - 6379:6379

      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install colorama requests pytest

      - name: Run database migrations
        run: |
          psql $DATABASE_URL -f supabase/migrations/20251127000000_add_metrics_hourly_aggregates.sql
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres

      - name: Start server
        run: |
          uvicorn src.main:app &
          sleep 5
        env:
          REDIS_URL: redis://localhost:6379
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres

      - name: Test monitoring stack
        run: |
          python scripts/test_monitoring_stack.py --verbose

      - name: Run pytest tests
        run: |
          pytest tests/routes/test_monitoring.py -v
          pytest tests/services/test_redis_metrics.py -v
```

---

## Summary

✅ **Quick Test**: `python scripts/test_monitoring_stack.py`

✅ **Verbose Test**: `python scripts/test_monitoring_stack.py --verbose`

✅ **Manual Tests**: Use curl commands above

✅ **Unit Tests**: `pytest tests/routes/test_monitoring.py tests/services/test_redis_metrics.py -v`

✅ **Load Test**: Use locust (optional)

**Expected Result**: All tests passing with 0 failures! 🎉
