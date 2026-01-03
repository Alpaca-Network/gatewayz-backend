# Testing Checklist: Admin Users Search Implementation

**Date**: January 3, 2026
**Feature**: `/admin/users` search and pagination
**Branch**: `feat/fix-metrics-volatility`

---

## Pre-Testing Setup

### 1. Apply Database Migration

**Staging/Development**:
```bash
# Apply the migration to add search indexes
psql -h your-db-host -U postgres -d your-database \
  -f supabase/migrations/20260103000000_add_admin_users_search_indexes.sql
```

**Local (if using local Supabase)**:
```bash
supabase migration up
```

### 2. Start API Server

```bash
# Start the server
python src/main.py

# Or with uvicorn
uvicorn src.main:app --reload
```

### 3. Get Admin API Key

Ensure you have an admin API key for testing:
```bash
# Check your .env file or Supabase dashboard
# Look for a user with role='admin'
```

---

## Automated Testing

### Run Test Script

```bash
# Set environment variables
export API_BASE_URL="http://localhost:8000"
export ADMIN_API_KEY="your_admin_api_key_here"

# Run test script
./scripts/test_admin_users_search.sh
```

**Expected Output**:
```
========================================
  Admin Users Search - Test Suite
========================================
API Base URL: http://localhost:8000

API is reachable
[TEST 1] No filters (backward compatibility)
✓ PASS (Status: 200)

[TEST 2] Email search (partial match)
✓ PASS (Status: 200)

... (all tests)

========================================
  Test Summary
========================================
Total Tests: 14
Passed: 14
Failed: 0
========================================
All tests passed!
```

---

## Manual Testing Checklist

### ✅ Basic Functionality

- [ ] **Test 1**: No filters (backward compatibility)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?limit=10"
```
**Expected**: Returns 10 users, statistics for all users

- [ ] **Test 2**: Email search (partial match)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=test&limit=10"
```
**Expected**: Returns users with "test" in email

- [ ] **Test 3**: API key search
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?api_key=gw_live&limit=10"
```
**Expected**: Returns users with "gw_live" in their API key

- [ ] **Test 4**: Active users filter
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?is_active=true&limit=10"
```
**Expected**: Returns only active users, statistics show only active

- [ ] **Test 5**: Inactive users filter
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?is_active=false&limit=10"
```
**Expected**: Returns only inactive users, statistics show only inactive

---

### ✅ Combined Filters

- [ ] **Test 6**: Email + Active status
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=gmail.com&is_active=true&limit=10"
```
**Expected**: Returns active Gmail users

- [ ] **Test 7**: API Key + Active status
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?api_key=gw_live&is_active=true&limit=10"
```
**Expected**: Returns active users with "gw_live" API keys

- [ ] **Test 8**: All three filters
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=test&api_key=gw&is_active=true&limit=10"
```
**Expected**: Returns users matching all criteria

---

### ✅ Pagination

- [ ] **Test 9**: Page 1 (offset=0)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?limit=10&offset=0"
```
**Expected**:
- Returns users 1-10
- `current_page: 1`
- `has_more: true` (if total > 10)

- [ ] **Test 10**: Page 2 (offset=10)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?limit=10&offset=10"
```
**Expected**:
- Returns users 11-20
- `current_page: 2`

- [ ] **Test 11**: Large limit (1000, maximum)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?limit=1000&offset=0"
```
**Expected**: Returns up to 1000 users

- [ ] **Test 12**: Minimum limit (1)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?limit=1&offset=0"
```
**Expected**: Returns exactly 1 user

---

### ✅ Edge Cases

- [ ] **Test 13**: Empty result (no matches)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=nonexistent_user_12345@test.com"
```
**Expected**:
```json
{
  "status": "success",
  "total_users": 0,
  "users": [],
  "statistics": {
    "active_users": 0,
    "inactive_users": 0,
    "total_credits": 0,
    "average_credits": 0
  }
}
```

- [ ] **Test 14**: Case insensitivity
```bash
# Search with uppercase
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=TEST@EXAMPLE.COM&limit=5"

# Should match "test@example.com"
```

- [ ] **Test 15**: Special characters
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=user%2Btest@example.com&limit=5"
```
**Expected**: Handles URL encoding correctly

- [ ] **Test 16**: Very long search string (>100 chars)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=verylongemailaddressthatexceedsnormallimitsshouldstillwork@test.com"
```
**Expected**: Handles gracefully (no crash)

---

### ✅ Response Validation

Verify response structure contains:

- [ ] `status` (should be "success")
- [ ] `total_users` (total matching filters, not total in DB)
- [ ] `has_more` (boolean, true if more results exist)
- [ ] `pagination` object with:
  - `limit`
  - `offset`
  - `current_page`
  - `total_pages`
- [ ] `filters_applied` object with:
  - `email`
  - `api_key`
  - `is_active`
- [ ] `statistics` object with:
  - `active_users`
  - `inactive_users`
  - `admin_users`
  - `developer_users`
  - `regular_users`
  - `total_credits`
  - `average_credits`
  - `subscription_breakdown`
- [ ] `users` array (current page)
- [ ] `timestamp` (ISO format)

---

### ✅ Statistics Accuracy

- [ ] **Test 17**: Verify statistics match filtered results

```bash
# Get filtered users
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=test&is_active=true&limit=1000" \
  | jq '.statistics'

# Manual verification:
# 1. Count active_users from response
# 2. Sum total_credits manually
# 3. Calculate average_credits
# 4. Verify all match the statistics object
```

- [ ] Active users count matches filter
- [ ] Inactive users count matches filter
- [ ] Total credits sum is accurate
- [ ] Average credits calculation is correct
- [ ] Subscription breakdown is accurate

---

### ✅ Performance Testing

- [ ] **Test 18**: Response time for small result set (1 user)
```bash
time curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=specific_email@test.com"
```
**Expected**: <50ms

- [ ] **Test 19**: Response time for medium result set (100 users)
```bash
time curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=gmail.com&limit=100"
```
**Expected**: <100ms

- [ ] **Test 20**: Response time for large result set (1000+ users)
```bash
time curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?is_active=true&limit=1000"
```
**Expected**: <300ms

- [ ] **Test 21**: Database query count
```sql
-- Enable query logging in PostgreSQL
-- Run a search query
-- Check logs: should see exactly 2 queries (data + statistics)
```

---

### ✅ Security Testing

- [ ] **Test 22**: SQL injection attempts
```bash
# Attempt SQL injection in email
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=' OR '1'='1"
```
**Expected**: No SQL injection (PostgREST handles parameterization)

- [ ] **Test 23**: XSS attempts
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users?email=<script>alert('xss')</script>"
```
**Expected**: Handles gracefully, no XSS

- [ ] **Test 24**: Unauthorized access (no API key)
```bash
curl "http://localhost:8000/admin/users?limit=10"
```
**Expected**: 401 Unauthorized

- [ ] **Test 25**: Non-admin access (regular user API key)
```bash
curl -H "Authorization: Bearer {regular_user_key}" \
  "http://localhost:8000/admin/users?limit=10"
```
**Expected**: 403 Forbidden

---

### ✅ Backward Compatibility

- [ ] **Test 26**: Existing frontend code (no parameters)
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users"
```
**Expected**:
- Returns first 100 users (default limit)
- Statistics for all users
- Same structure as before (with new fields added)

---

## Integration Testing (with Frontend)

### ✅ Frontend Integration

- [ ] **Test 27**: Search from frontend admin panel
  - Open admin dashboard
  - Enter email in search box
  - Click search
  - Verify results display

- [ ] **Test 28**: Pagination from frontend
  - Search for users (should have multiple pages)
  - Click "Next Page"
  - Verify correct users display
  - Verify page number updates

- [ ] **Test 29**: Combined filters from frontend
  - Enter email filter
  - Select "Active Only"
  - Click search
  - Verify results match both criteria

---

## Regression Testing

### ✅ Existing Functionality

- [ ] **Test 30**: `/admin/balance` still works
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/balance"
```

- [ ] **Test 31**: `/admin/monitor` still works
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/monitor"
```

- [ ] **Test 32**: `/admin/users/{user_id}` still works
```bash
curl -H "Authorization: Bearer {admin_key}" \
  "http://localhost:8000/admin/users/123"
```

---

## Post-Deployment Verification

### ✅ Production Checks

- [ ] **Test 33**: Verify indexes exist
```sql
SELECT * FROM pg_indexes
WHERE indexname LIKE 'idx_users_%'
OR indexname LIKE 'idx_api_keys_%';
```
**Expected**: 7 indexes created

- [ ] **Test 34**: Monitor query performance
```sql
-- Check slow query log
SELECT * FROM pg_stat_statements
WHERE query LIKE '%admin%users%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```
**Expected**: Mean execution time <100ms

- [ ] **Test 35**: Check error logs
```bash
# Check Sentry or application logs
# Verify no new errors related to /admin/users
```

- [ ] **Test 36**: Monitor API response times
```promql
# Prometheus query
histogram_quantile(0.95, http_request_duration_seconds{endpoint="/admin/users"})
```
**Expected**: <500ms (95th percentile)

---

## Sign-off Checklist

### Before Merging to Main

- [ ] All automated tests passing
- [ ] All manual tests checked
- [ ] Performance benchmarks met
- [ ] Security tests passed
- [ ] Backward compatibility verified
- [ ] Frontend integration tested
- [ ] Documentation complete
- [ ] Code review approved
- [ ] Database migration tested on staging
- [ ] No regressions in existing functionality

### Deployment Approval

- [ ] **Technical Lead**: Approved
- [ ] **Product Manager**: Approved
- [ ] **QA Lead**: Approved
- [ ] **DevOps**: Migration ready

---

## Test Results

### Test Summary

| Category | Total | Passed | Failed | Notes |
|----------|-------|--------|--------|-------|
| Basic Functionality | 5 | __ | __ | |
| Combined Filters | 3 | __ | __ | |
| Pagination | 4 | __ | __ | |
| Edge Cases | 4 | __ | __ | |
| Response Validation | 1 | __ | __ | |
| Statistics Accuracy | 1 | __ | __ | |
| Performance | 3 | __ | __ | |
| Security | 4 | __ | __ | |
| Backward Compatibility | 1 | __ | __ | |
| Integration | 3 | __ | __ | |
| Regression | 3 | __ | __ | |
| Post-Deployment | 4 | __ | __ | |
| **TOTAL** | **36** | __ | __ | |

---

**Tester**: ____________________
**Date**: ____________________
**Environment**: [ ] Local [ ] Staging [ ] Production
**Result**: [ ] PASS [ ] FAIL
**Notes**: ____________________________________________________

---

**Last Updated**: January 3, 2026
