# Test Status Guide - Remaining Items

## Current Test Status ✅

```
✅ 953 tests PASSING (0 failures!)
⏭️  387 tests SKIPPED (intentional)
❌ 38 tests XFAILED (expected failures)
⚠️  2 tests XPASSED (unexpected passes)
```

---

## Understanding Test Statuses

### 1. SKIPPED Tests (387 tests) - ⏭️ INTENTIONAL

**What are they?**
Tests marked with `@pytest.mark.skip` or `@pytest.mark.skipif` that are intentionally not run.

**Why skip tests?**
- **Missing dependencies**: Features not yet implemented
- **Conditional environments**: Tests only run in specific environments
- **Smoke tests**: Require running application (now skipped by default)
- **Integration tests**: Require external services

**Categories:**

#### A. Smoke Tests (24 tests)
```bash
# Location: tests/smoke/
# Reason: Require running application on localhost:8000
```
**How to run:**
```bash
# Start the application first, then:
pytest -m smoke -v
```

#### B. Feature Not Implemented (majority)
```bash
# Common skips:
# - Cache not implemented yet (tests/services/test_response_cache.py)
# - Health monitor not implemented yet (tests/services/test_model_health_monitor.py)
# - Google Vertex AI SDK not available
# - Concurrency limiting temporarily disabled
```

**How to address:**
1. Identify the feature: `pytest tests/ -v | grep SKIP`
2. Check the skip reason in the test file
3. Implement the feature
4. Remove the `@pytest.mark.skip` decorator

**Example:**
```python
# Before:
@pytest.mark.skipif(not CACHE_AVAILABLE, reason="Cache not implemented yet")
def test_cache_functionality():
    ...

# After implementing cache:
# Remove the decorator
def test_cache_functionality():
    ...
```

---

### 2. XFAILED Tests (38 tests) - ❌ EXPECTED FAILURES

**What are they?**
Tests marked with `@pytest.mark.xfail` - they're expected to fail but tracked.

**Why mark tests as xfail?**
- Known bugs being tracked
- Features in development
- Breaking changes being planned
- Authentication/mocking issues (current main reason)

**Current xfail categories:**

#### A. Authentication Mocking Issues (majority - 38 tests)
```bash
# Location: tests/routes/test_activity.py, tests/routes/test_analytics.py
# Reason: "Authentication mocking issue - endpoint returns 403"
```

**How to fix:**
Apply the same pattern we used for other tests:

```python
# Before (failing with 403):
def test_activity_stats(client, auth_headers):
    response = client.get('/activity/stats', headers=auth_headers)
    assert response.status_code == 200  # ❌ Gets 403

# After (using same pattern as test_chat.py):
@patch('src.routes.activity.get_user')  # Patch where USED
def test_activity_stats(mock_get_user, client, auth_headers):
    mock_get_user.return_value = {
        'id': 1,
        'email': 'test@example.com',
        'credits': 100.0
    }
    response = client.get('/activity/stats', headers=auth_headers)
    assert response.status_code == 200  # ✅ Now works
```

**Step-by-step to fix all xfailed tests:**

1. **Identify the xfailed tests:**
```bash
pytest tests/routes/test_activity.py tests/routes/test_analytics.py -v | grep XFAIL
```

2. **Apply the mocking pattern:**
```bash
# For each test file:
# 1. Add @patch decorators for functions imported in the route
# 2. Mock get_user at usage site (src.routes.activity.get_user)
# 3. Mock any database/external calls
# 4. Remove @pytest.mark.xfail decorator
```

3. **Example fix for test_activity.py:**
```python
# Find what the route imports:
# src/routes/activity.py:
# from src.db.users import get_user
# from src.db.activity import log_activity, get_activity_stats

# Patch at usage site:
@patch('src.routes.activity.get_activity_stats')
@patch('src.routes.activity.get_user')
def test_get_activity_stats_default(mock_get_user, mock_get_stats, client, auth_headers):
    mock_get_user.return_value = {'id': 1, 'email': 'test@example.com'}
    mock_get_stats.return_value = {'total_requests': 100, 'successful': 95}

    response = client.get('/activity/stats', headers=auth_headers)
    assert response.status_code == 200
```

---

### 3. XPASSED Tests (2 tests) - ⚠️ UNEXPECTED PASSES

**What are they?**
Tests marked as `@pytest.mark.xfail` but they unexpectedly passed.

**Current xpassed:**
```bash
tests/routes/test_activity.py::TestActivityAuthentication::test_log_requires_authentication
# This test was expected to fail but now passes!
```

**How to handle:**
1. **Verify the test actually works:**
```bash
pytest tests/routes/test_activity.py::TestActivityAuthentication::test_log_requires_authentication -v
```

2. **If it consistently passes, remove the xfail marker:**
```python
# Before:
@pytest.mark.xfail(reason="Authentication mocking issue")
def test_log_requires_authentication(self, client):
    ...

# After:
def test_log_requires_authentication(self, client):
    # Test is now working correctly
    ...
```

3. **Run again to confirm:**
```bash
pytest tests/routes/test_activity.py -v
```

---

## Action Plan to Squash All Remaining Issues

### Priority 1: Fix XFAILED Tests (Highest Impact) 🎯

**Estimated effort:** 2-4 hours

```bash
# Step 1: Create a branch
git checkout -b fix/remaining-xfailed-tests

# Step 2: Fix test_activity.py xfails (9 tests)
# - Apply @patch('src.routes.activity.get_user') pattern
# - Mock get_activity_stats, log_activity functions
# - Remove @pytest.mark.xfail decorators

# Step 3: Fix test_analytics.py xfails (29 tests)
# - Apply @patch('src.routes.analytics.get_user') pattern
# - Mock analytics functions
# - Remove @pytest.mark.xfail decorators

# Step 4: Remove xfail from xpassed tests (2 tests)

# Step 5: Run tests
pytest tests/routes/test_activity.py tests/routes/test_analytics.py -v

# Step 6: Commit and push
git add tests/routes/test_activity.py tests/routes/test_analytics.py
git commit -m "Fix all xfailed tests in activity and analytics"
git push -u origin fix/remaining-xfailed-tests
```

### Priority 2: Address SKIPPED Tests (Lower Priority) 📋

**Estimated effort:** Varies by feature

```bash
# Step 1: Categorize skipped tests
pytest tests/ -v 2>&1 | grep SKIP > skipped_tests.txt

# Step 2: Prioritize by business value
# - Smoke tests: Already handled (skip by default, run in CI/CD)
# - Cache tests: Implement cache if needed for performance
# - Health monitor: Implement if needed for observability
# - Google Vertex: Install SDK if using Google AI

# Step 3: Create issues for each feature
# Example for cache:
# Title: "Implement response cache for API optimization"
# Description: "Currently 8 tests are skipped. Need to implement caching layer."
```

---

## Quick Commands Reference

```bash
# Run only non-skipped tests (current setup)
pytest tests/

# Run smoke tests (requires running app)
BASE_URL=http://localhost:8000 pytest -m smoke -v

# Show all skipped tests
pytest tests/ -v | grep SKIP

# Show all xfailed tests
pytest tests/ -v | grep XFAIL

# Show all xpassed tests
pytest tests/ -v | grep XPASS

# Run specific problematic test files
pytest tests/routes/test_activity.py tests/routes/test_analytics.py -v

# Run a single xfailed test to debug
pytest tests/routes/test_activity.py::test_get_activity_stats_default -v --tb=short

# Count tests by status
pytest tests/ -v --tb=no | tail -3
```

---

## Template for Fixing XFAILED Test

```python
# File: tests/routes/test_activity.py

# BEFORE (xfailed):
@pytest.mark.xfail(reason="Authentication mocking issue - endpoint returns 403")
def test_get_activity_stats_default(self, client, auth_headers):
    response = client.get('/activity/stats', headers=auth_headers)
    assert response.status_code == 200

# AFTER (fixed):
from unittest.mock import patch

@patch('src.routes.activity.get_activity_stats')  # Patch where USED not DEFINED
@patch('src.routes.activity.get_user')
def test_get_activity_stats_default(self, mock_get_user, mock_get_stats, client, auth_headers):
    # Setup mocks
    mock_get_user.return_value = {
        'id': 1,
        'user_id': 1,
        'email': 'test@example.com',
        'credits': 100.0,
        'api_key': 'gw_test_key_123'
    }
    mock_get_stats.return_value = {
        'total_requests': 100,
        'successful_requests': 95,
        'failed_requests': 5,
        'avg_response_time': 150.5
    }

    # Make request
    response = client.get('/activity/stats', headers=auth_headers)

    # Assertions
    assert response.status_code == 200
    data = response.json()
    assert data['total_requests'] == 100
    assert data['successful_requests'] == 95
```

---

## Success Criteria

When all remaining issues are addressed:

```bash
# Target:
✅ 991+ tests PASSING (953 + 38 xfailed fixed)
⏭️  ~360 tests SKIPPED (smoke + unimplemented features - acceptable)
❌ 0 tests XFAILED
⚠️  0 tests XPASSED
```

---

## Notes

- **SKIPPED tests are OK** - They represent unimplemented features or conditional tests
- **XFAILED tests should be fixed** - They represent known issues that need attention
- **XPASSED tests need investigation** - Remove xfail marker if they consistently pass
- **All actual failures (0) are fixed** - Great job! 🎉

---

## Need Help?

1. **For authentication mocking issues:** Reference `tests/routes/test_chat.py` as the template
2. **For database mocking:** Look at `tests/routes/test_admin.py`
3. **For async issues:** Check `tests/routes/test_health.py`

