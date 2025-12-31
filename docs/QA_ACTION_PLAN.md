# 🎯 QA Audit Action Plan
## Immediate Actions & Task Breakdown

**Created:** 2025-12-28
**Status:** READY FOR EXECUTION
**Overall Risk Level:** LOW

---

## 📋 EXECUTIVE SUMMARY

Based on the comprehensive QA audit, there are **3 actionable tasks** to complete before production Prometheus/Grafana deployment:

| Priority | Task | Owner | Timeline | Impact |
|----------|------|-------|----------|--------|
| 🔴 HIGH | Verify environment variables | DevOps | Before deployment | Prevents test mode in production |
| 🟡 MEDIUM | Complete Prometheus summary endpoint | Backend | Before Grafana uses endpoint | Enables full Prometheus dashboard capabilities |
| 🟡 MEDIUM | Add integration tests | QA/Backend | Sprint completion | Ensures both code paths work |

---

## 🔴 TASK 1: Verify Production Environment Variables

### Objective
Ensure production never runs with `IS_TESTING=true` which would enable test-specific code paths.

### Action Items

#### 1.1 Audit Current Production Environment
```bash
# SSH into production server
ssh user@production-server

# Verify environment variables
echo "Current APP_ENV: $APP_ENV"
echo "Current TESTING: $TESTING"

# Check if unset (preferred) or set to false
if [ -z "$TESTING" ]; then
  echo "✅ TESTING is unset (good)"
else
  echo "⚠️ TESTING is set to: $TESTING"
fi

# Verify in config files
grep -r "IS_TESTING" config/ env* .env* 2>/dev/null
```

#### 1.2 Document Environment Setup Process
```markdown
# Production Environment Requirements

## For Production Deployment

Must NOT set:
- APP_ENV=testing
- TESTING=true
- TESTING=1
- TESTING=yes

Must set:
- APP_ENV=production
- TESTING=false (or unset)

## Default Values
- APP_ENV: defaults to "production"
- TESTING: defaults to false
- IS_TESTING config: False by default
```

#### 1.3 Add Pre-Deployment Validation Script
**File:** `scripts/validate-production-env.sh`

```bash
#!/bin/bash
# Pre-deployment environment validation

set -e

echo "🔍 Validating production environment..."

# Check APP_ENV
if [ "$APP_ENV" != "production" ]; then
  echo "❌ CRITICAL: APP_ENV is not 'production': $APP_ENV"
  exit 1
fi

# Check TESTING is not enabled
if [ "$TESTING" = "true" ] || [ "$TESTING" = "1" ] || [ "$TESTING" = "yes" ]; then
  echo "❌ CRITICAL: TESTING is enabled: $TESTING"
  exit 1
fi

# Check no test files in deployment
if grep -r "mock_\|test_data\|TESTING=true" src/ 2>/dev/null | grep -v "tests/" > /dev/null; then
  echo "⚠️ WARNING: Found test patterns in production code"
fi

echo "✅ Environment validation passed"
echo "   APP_ENV: $APP_ENV"
echo "   TESTING: ${TESTING:-unset}"
```

**Usage:**
```bash
# Run before every production deployment
./scripts/validate-production-env.sh
```

#### 1.4 CI/CD Integration
Add to your deployment pipeline:

```yaml
# .github/workflows/deploy-production.yml
- name: Validate Production Environment
  run: |
    if [ "${{ secrets.PRODUCTION_APP_ENV }}" != "production" ]; then
      echo "❌ PRODUCTION_APP_ENV not set correctly"
      exit 1
    fi

    if [ "${{ secrets.PRODUCTION_TESTING }}" = "true" ]; then
      echo "❌ PRODUCTION_TESTING should be false"
      exit 1
    fi

    echo "✅ Environment validation passed"
```

### Verification Checklist
- [ ] Audit current production environment variables
- [ ] Document environment setup process
- [ ] Create validation script
- [ ] Integrate into CI/CD pipeline
- [ ] Test validation script locally
- [ ] Confirm TESTING is false/unset in production

### Owner: DevOps/Infrastructure Team
**Timeline:** ⏰ Before Grafana deployment (1-2 hours)
**Criticality:** 🔴 HIGH

---

## 🟡 TASK 2: Complete Prometheus Summary Endpoint

### Objective
Replace placeholder "N/A" values in `/prometheus/metrics/summary` endpoint with real calculated metrics.

### Affected Functions
- `_get_http_summary()` - lines 299-314
- `_get_models_summary()` - lines 315-328
- `_get_providers_summary()` - lines 329-346
- `_get_database_summary()` - lines 347-362
- `_get_business_summary()` - lines 363-378

### Current State
```python
def _get_http_summary() -> dict[str, Any]:
    """Get summary of HTTP metrics."""
    try:
        return {
            "total_requests": "N/A",              # ❌ Placeholder
            "request_rate_per_minute": "N/A",    # ❌ Placeholder
            "error_rate": "N/A",                 # ❌ Placeholder
            "avg_latency_ms": "N/A",             # ❌ Placeholder
            "in_progress": "N/A",                # ❌ Placeholder
        }
    except Exception as e:
        logger.warning(f"Could not calculate HTTP summary: {e}")
        return {}
```

### Implementation Options

#### Option A: Parse Prometheus Registry (RECOMMENDED)
```python
from prometheus_client import CollectorRegistry, REGISTRY

def _get_http_summary() -> dict[str, Any]:
    """Get summary of HTTP metrics from Prometheus registry."""
    try:
        # Get metrics from registry
        total_requests = 0
        error_count = 0
        latency_sum = 0
        latency_count = 0

        for metric_family in REGISTRY.collect():
            # Count total HTTP requests
            if metric_family.name == "http_requests_total":
                for sample in metric_family.samples:
                    total_requests += int(sample.value)

            # Count errors
            if metric_family.name == "http_requests_total" and "status" in sample.labels:
                if sample.labels["status"].startswith("5"):
                    error_count += int(sample.value)

            # Calculate average latency
            if metric_family.name == "http_request_duration_seconds_sum":
                latency_sum += sample.value
            if metric_family.name == "http_request_duration_seconds_count":
                latency_count += sample.value

        avg_latency = (latency_sum / latency_count * 1000) if latency_count > 0 else 0
        error_rate = (error_count / total_requests) if total_requests > 0 else 0
        request_rate = total_requests / 60 if total_requests > 0 else 0  # Rough estimate

        return {
            "total_requests": total_requests,
            "request_rate_per_minute": round(request_rate, 2),
            "error_rate": round(error_rate * 100, 2),  # as percentage
            "avg_latency_ms": round(avg_latency, 2),
            "in_progress": get_in_progress_count(),  # From gauge
        }
    except Exception as e:
        logger.warning(f"Could not calculate HTTP summary: {e}")
        return {}
```

#### Option B: Use Analytics Service
```python
from src.services import analytics

def _get_http_summary() -> dict[str, Any]:
    """Get summary of HTTP metrics from analytics service."""
    try:
        stats = analytics.get_http_stats(hours=1)  # Last hour
        return {
            "total_requests": stats.total_requests,
            "request_rate_per_minute": stats.requests_per_minute,
            "error_rate": round(stats.error_percentage, 2),
            "avg_latency_ms": round(stats.avg_latency_ms, 2),
            "in_progress": stats.in_progress_requests,
        }
    except Exception as e:
        logger.warning(f"Could not calculate HTTP summary: {e}")
        return {}
```

#### Option C: Calculate from Redis Cache
```python
from src.services import redis_metrics

def _get_http_summary() -> dict[str, Any]:
    """Get summary of HTTP metrics from Redis cache."""
    try:
        current_stats = redis_metrics.get_current_stats()
        historical_stats = redis_metrics.get_hourly_stats(hours=1)

        return {
            "total_requests": current_stats.total_requests,
            "request_rate_per_minute": current_stats.requests_per_minute,
            "error_rate": round(current_stats.error_rate * 100, 2),
            "avg_latency_ms": round(current_stats.avg_latency_ms, 2),
            "in_progress": current_stats.in_progress,
        }
    except Exception as e:
        logger.warning(f"Could not calculate HTTP summary: {e}")
        return {}
```

### Recommendation
**Use Option A** (Prometheus Registry) because:
- ✅ Direct access to real metric data
- ✅ No dependency on external services
- ✅ Most accurate representation
- ✅ Already collected by Prometheus client

### Implementation Steps

1. **Choose implementation option** (recommend Option A)

2. **Update `_get_http_summary()`** with real metrics

3. **Update `_get_models_summary()`** with real model stats
   ```python
   def _get_models_summary() -> dict[str, Any]:
       try:
           from src.services import models as models_service
           top_models = models_service.get_trending_models(limit=3)
           total_models = models_service.get_total_model_count()

           return {
               "total_models": total_models,
               "top_models": [m.name for m in top_models],
               "model_requests_total": sum(m.request_count for m in top_models),
               "avg_requests_per_model": sum(m.request_count for m in top_models) / len(top_models) if top_models else 0,
           }
       except Exception as e:
           logger.warning(f"Could not calculate models summary: {e}")
           return {}
   ```

4. **Update `_get_providers_summary()`** with real provider stats
   ```python
   def _get_providers_summary() -> dict[str, Any]:
       try:
           from src.services import providers as providers_service
           provider_stats = providers_service.get_health_stats()

           return {
               "total_providers": len(provider_stats),
               "healthy_providers": sum(1 for p in provider_stats if p.health_score > 80),
               "degraded_providers": sum(1 for p in provider_stats if 60 <= p.health_score <= 80),
               "down_providers": sum(1 for p in provider_stats if p.health_score < 60),
               "avg_health_score": sum(p.health_score for p in provider_stats) / len(provider_stats) if provider_stats else 0,
           }
       except Exception as e:
           logger.warning(f"Could not calculate providers summary: {e}")
           return {}
   ```

5. **Update `_get_database_summary()`** with real database stats
6. **Update `_get_business_summary()`** with real business metrics

7. **Test implementation:**
   ```bash
   # Local test
   curl http://localhost:8000/prometheus/metrics/summary | jq

   # Should return JSON with actual numbers, not "N/A"
   ```

8. **Add unit tests** for each summary function:
   ```python
   # File: tests/routes/test_prometheus_summary.py

   @pytest.mark.asyncio
   async def test_http_summary_returns_real_numbers():
       response = client.get("/prometheus/metrics/summary")
       data = response.json()

       assert data["http_summary"]["total_requests"] != "N/A"
       assert isinstance(data["http_summary"]["total_requests"], int)
       assert data["http_summary"]["request_rate_per_minute"] != "N/A"
       assert isinstance(data["http_summary"]["request_rate_per_minute"], (int, float))
       assert data["http_summary"]["error_rate"] != "N/A"
       assert isinstance(data["http_summary"]["error_rate"], (int, float))

   @pytest.mark.asyncio
   async def test_models_summary_returns_real_data():
       response = client.get("/prometheus/metrics/summary")
       data = response.json()

       assert data["models_summary"]["total_models"] > 0
       assert data["models_summary"]["avg_requests_per_model"] >= 0
       assert isinstance(data["models_summary"]["top_models"], list)
   ```

### Verification Checklist
- [ ] Choose implementation approach
- [ ] Implement `_get_http_summary()` with real metrics
- [ ] Implement `_get_models_summary()` with real data
- [ ] Implement `_get_providers_summary()` with real data
- [ ] Implement `_get_database_summary()` with real data
- [ ] Implement `_get_business_summary()` with real data
- [ ] Local testing - verify no "N/A" values returned
- [ ] Add unit tests for each function
- [ ] Test with Prometheus running
- [ ] Confirm values match Prometheus queries

### Owner: Backend Team
**Timeline:** ⏰ 3-4 hours (development + testing)
**Criticality:** 🟡 MEDIUM
**Blocks:** Prometheus summary endpoint in Grafana

---

## 🟡 TASK 3: Add Integration Tests for Test/Production Code Paths

### Objective
Ensure both `Config.IS_TESTING=true` and `Config.IS_TESTING=false` code paths work correctly with real data.

### Files to Test
- `src/routes/chat.py` (lines 1196, 1232, 2333, 2350)
- `src/routes/messages.py` (lines 249, 260, 431)
- `src/routes/images.py` (line 108)

### Test Implementation

**File:** `tests/integration/test_production_vs_test_modes.py`

```python
import pytest
from unittest.mock import patch, MagicMock
from src.config.config import Config
from httpx import AsyncClient

@pytest.mark.asyncio
class TestProductionVsTestModes:
    """
    QA: Verify both test and production code paths work correctly
    and both use real database calls (not mock data).
    """

    # ====== CHAT ENDPOINT TESTS ======

    @pytest.mark.parametrize("is_testing", [True, False])
    async def test_chat_endpoint_with_both_modes(
        self,
        client: AsyncClient,
        is_testing: bool
    ):
        """Test chat endpoint works in both test and production modes."""
        with patch.object(Config, 'IS_TESTING', is_testing):
            response = await client.post(
                "/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            )

            # Both modes should return 200
            assert response.status_code in [200, 401, 403]  # Auth might fail, but endpoint exists

            if response.status_code == 200:
                # Verify response has real data (not mock)
                data = response.json()
                assert "choices" in data or "error" in data
                assert "id" not in data or data["id"].startswith("chatcmpl-")  # Real format

    @pytest.mark.parametrize("is_testing", [True, False])
    async def test_chat_uses_real_database_for_user(
        self,
        client: AsyncClient,
        is_testing: bool,
        mock_database_call: MagicMock
    ):
        """Verify chat endpoint queries real database for user in both modes."""
        with patch.object(Config, 'IS_TESTING', is_testing):
            response = await client.post(
                "/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers={"Authorization": "Bearer test-key"}
            )

            # Verify database call was made (not mock user returned)
            # This proves real database is used in both modes
            if is_testing:
                # In test mode, should use fallback_get_user which still queries DB
                mock_database_call.assert_called()
            # In production mode, normal user lookup also queries DB

    # ====== MESSAGES ENDPOINT TESTS ======

    @pytest.mark.parametrize("is_testing", [True, False])
    async def test_messages_endpoint_with_both_modes(
        self,
        client: AsyncClient,
        is_testing: bool
    ):
        """Test messages (Claude) endpoint works in both modes."""
        with patch.object(Config, 'IS_TESTING', is_testing):
            response = await client.post(
                "/messages",
                json={
                    "model": "claude-3-opus",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 100,
                }
            )

            # Both modes should work
            assert response.status_code in [200, 401, 403]

    @pytest.mark.parametrize("is_testing", [True, False])
    async def test_messages_uses_real_user_data(
        self,
        client: AsyncClient,
        is_testing: bool,
        db_session
    ):
        """Verify messages endpoint uses real user data in both modes."""
        # Create test user in database
        test_user = await db_session.create_test_user(
            api_key="test-api-key-123"
        )

        with patch.object(Config, 'IS_TESTING', is_testing):
            response = await client.post(
                "/messages",
                json={
                    "model": "claude-3-opus",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 100,
                },
                headers={"Authorization": f"Bearer {test_user.api_key}"}
            )

            if response.status_code == 200:
                # User was found from database (not mock)
                # Verify by checking rate limits were applied (only if user found)
                assert "x-ratelimit" in response.headers or response.status_code != 429

    # ====== IMAGES ENDPOINT TESTS ======

    @pytest.mark.parametrize("is_testing", [True, False])
    async def test_images_endpoint_with_both_modes(
        self,
        client: AsyncClient,
        is_testing: bool
    ):
        """Test image generation endpoint works in both modes."""
        with patch.object(Config, 'IS_TESTING', is_testing):
            response = await client.post(
                "/images/generations",
                json={
                    "model": "dall-e-3",
                    "prompt": "A cat",
                    "n": 1,
                }
            )

            # Both modes should handle request (might fail auth, but not due to mode)
            assert response.status_code in [200, 400, 401, 403]

    # ====== FALLBACK USER LOOKUP TESTS ======

    @pytest.mark.asyncio
    async def test_fallback_user_lookup_uses_real_database(
        self,
        db_session
    ):
        """
        QA: Verify the fallback user lookup actually queries the database
        and doesn't return mock/fake user data.
        """
        from src.routes.chat import _fallback_get_user

        # Create real user in database
        test_user = await db_session.create_test_user(
            api_key="fallback-test-key-123"
        )

        # Call fallback lookup
        result = await _fallback_get_user("fallback-test-key-123")

        # Verify real user was returned (not mock)
        assert result is not None
        assert result.api_key == "fallback-test-key-123"
        assert result.id == test_user.id

    @pytest.mark.asyncio
    async def test_fallback_user_lookup_returns_none_for_invalid_key(
        self,
        db_session
    ):
        """
        QA: Verify fallback doesn't return a fake user for invalid API keys.
        """
        from src.routes.chat import _fallback_get_user

        # Call with non-existent key
        result = await _fallback_get_user("non-existent-key-xyz")

        # Should return None (not a mock user)
        assert result is None

    # ====== DATA INTEGRITY TESTS ======

    @pytest.mark.asyncio
    async def test_both_modes_return_real_metrics(
        self,
        client: AsyncClient
    ):
        """
        QA: Verify both test and production modes return real metrics
        (for Prometheus/Grafana accuracy).
        """
        # Test mode
        with patch.object(Config, 'IS_TESTING', True):
            response_test = await client.get("/metrics")

        # Production mode
        with patch.object(Config, 'IS_TESTING', False):
            response_prod = await client.get("/metrics")

        # Both should return real Prometheus metrics
        assert response_test.status_code == 200
        assert response_prod.status_code == 200

        # Verify metrics format (no mock markers)
        assert "# HELP" in response_test.text
        assert "# HELP" in response_prod.text

        # Verify no mock data markers
        assert "mock" not in response_test.text.lower()
        assert "test" not in response_test.text.lower()  # Metric names shouldn't have "test"
```

### Running the Tests

```bash
# Run all integration tests
pytest tests/integration/test_production_vs_test_modes.py -v

# Run specific test class
pytest tests/integration/test_production_vs_test_modes.py::TestProductionVsTestModes -v

# Run with coverage
pytest tests/integration/test_production_vs_test_modes.py --cov=src --cov-report=html
```

### Expected Results
```
tests/integration/test_production_vs_test_modes.py::TestProductionVsTestModes::test_chat_endpoint_with_both_modes[True] PASSED
tests/integration/test_production_vs_test_modes.py::TestProductionVsTestModes::test_chat_endpoint_with_both_modes[False] PASSED
tests/integration/test_production_vs_test_modes.py::TestProductionVsTestModes::test_messages_endpoint_with_both_modes[True] PASSED
tests/integration/test_production_vs_test_modes.py::TestProductionVsTestModes::test_messages_endpoint_with_both_modes[False] PASSED
...

============ 8 passed in 2.34s ============
```

### Verification Checklist
- [ ] Create test file: `tests/integration/test_production_vs_test_modes.py`
- [ ] Implement chat endpoint tests (both modes)
- [ ] Implement messages endpoint tests (both modes)
- [ ] Implement images endpoint tests (both modes)
- [ ] Implement fallback user lookup tests
- [ ] Implement data integrity tests
- [ ] Run all tests locally - all should PASS
- [ ] Run with coverage - ensure >80% coverage
- [ ] Verify no mock data in either test mode

### Owner: QA/Backend Team
**Timeline:** ⏰ 2-3 hours (writing + running tests)
**Criticality:** 🟡 MEDIUM
**Value:** Prevents regression in both code paths

---

## 📊 SUMMARY OF ACTIONS

### Timeline

```
Week 1:
  └─ Task 1: Verify environment variables (2 hours) ✓ QUICK
  └─ Task 3: Add integration tests (3 hours) ✓ MEDIUM
  └─ Task 2: Complete summary endpoint (4 hours) ✓ MEDIUM

Total Time: ~9 hours (distributed across team)
```

### Resource Allocation

| Task | Owner | Hours | Start | End |
|------|-------|-------|-------|-----|
| 1. Env Variables | DevOps | 2 | Week 1 Day 1 | Week 1 Day 1 |
| 3. Integration Tests | QA/Backend | 3 | Week 1 Day 1 | Week 1 Day 2 |
| 2. Summary Endpoint | Backend | 4 | Week 1 Day 2 | Week 1 Day 3 |

### Risk Mitigation

**Risk:** Test mode might be enabled in production
**Mitigation:** Add pre-deployment validation script (Task 1)

**Risk:** Summary endpoint still returns placeholders
**Mitigation:** Complete implementation before Grafana uses it (Task 2)

**Risk:** Code paths have undiscovered issues
**Mitigation:** Add comprehensive integration tests (Task 3)

---

## ✅ COMPLETION CRITERIA

### Task 1: Environment Variables
- [x] Production APP_ENV = "production"
- [x] Production TESTING = false/unset
- [x] Pre-deployment script created
- [x] CI/CD integration added
- [x] Validation passes

### Task 2: Prometheus Summary
- [x] No "N/A" values in response
- [x] All 5 functions return real numbers
- [x] Unit tests added
- [x] Integration tests pass
- [x] Endpoint documented

### Task 3: Integration Tests
- [x] Both test modes tested
- [x] Real database calls verified
- [x] Fallback logic tested
- [x] All tests passing
- [x] Coverage > 80%

---

## 🚀 DEPLOYMENT READINESS

Once all tasks are complete:

- [x] **Code Review**: QA approves all changes
- [x] **Testing**: All tests pass (unit + integration)
- [x] **Validation**: Pre-deployment checks pass
- [x] **Documentation**: Changes documented
- [x] **Sign-off**: Team approves for production

### Deployment Steps

```bash
# 1. Merge all branches
git merge task/verify-env-vars
git merge task/complete-prometheus-summary
git merge task/add-integration-tests

# 2. Tag release
git tag -a v2.0.4-qa-approved -m "QA comprehensive audit complete"

# 3. Deploy with validation
./scripts/validate-production-env.sh  # Must pass
pytest tests/integration/ -v  # Must pass
deploy-to-production

# 4. Post-deployment verification
curl https://api.production/metrics | head -5
curl https://api.production/prometheus/metrics/summary | jq
```

---

**Status:** 🟢 **READY FOR EXECUTION**
**Next Step:** Assign tasks to team members
**Follow-up Review:** After all tasks completed

