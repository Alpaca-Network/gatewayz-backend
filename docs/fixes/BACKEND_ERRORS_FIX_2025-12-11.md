# Backend Error Fix - December 11, 2025

## Issue: 429 Rate Limit Errors for Valid Requests

### Symptoms
- Users receiving `429 Too Many Requests` errors on `/v1/chat/completions` endpoint
- Errors occurring even when concurrency check shows `0/50` (well under limit)
- Log message: "Rate limit alerts table not available, skipping alert creation"
- All requests from the same API key being rate limited regardless of actual usage

### Root Cause Analysis

**Location**: `src/db/rate_limits.py:411` and `src/db/rate_limits.py:426`

The `burst_limit` configuration was incorrectly set to `10` instead of `100`.

#### Technical Details

1. **Burst Limiting Algorithm**: The system uses a token bucket algorithm for burst limiting
   - Tokens are refilled at a rate of `burst_limit / 60` tokens per second
   - Each request consumes 1 burst token
   - When burst tokens reach 0, requests are rate limited

2. **The Bug**:
   ```python
   # BEFORE (src/db/rate_limits.py:411)
   "burst_limit": config.get("burst_limit", 10),  # ❌ TOO LOW

   # BEFORE (src/db/rate_limits.py:426)
   "burst_limit": 10,  # ❌ TOO LOW
   ```

3. **Impact**:
   - With `burst_limit=10`: Refill rate = 10/60 = 0.166 tokens/second
   - This means only 1 token every 6 seconds
   - After initial 10 requests, users could only make 1 request every 6 seconds
   - This effectively created a severe bottleneck

4. **Why It Affects All Requests**:
   - The fallback rate limiter (`src/services/rate_limiting_fallback.py`) uses `defaultdict(int)`
   - New API keys start with 0 burst tokens
   - The refill logic adds tokens, but with burst_limit=10, the refill is too slow
   - Once the initial 10 tokens are depleted, the system can't keep up with normal traffic

### Fix Applied

**Files Changed**: `src/db/rate_limits.py`

**All 4 locations updated** (lines 411, 426, 479, 491):

```python
# Location 1: get_rate_limit_config() - database config (line 411)
"burst_limit": config.get("burst_limit", 100),  # ✅ FIXED (was 10)

# Location 2: get_rate_limit_config() - fallback default (line 426)
"burst_limit": 100,  # ✅ FIXED (was 10)

# Location 3: update_rate_limit_config() - UPDATE query (line 479)
"burst_limit": config.get("burst_limit", 100),  # ✅ FIXED (was 10)

# Location 4: update_rate_limit_config() - INSERT query (line 491)
"burst_limit": config.get("burst_limit", 100),  # ✅ FIXED (was 10)
```

**Critical**: Locations 3 & 4 were identified by Sentry AI bot as causing config updates via API to revert to the old default of 10, effectively undoing the fix. All locations must be updated to prevent regression.

**Result**:
- With `burst_limit=100`: Refill rate = 100/60 = 1.666 tokens/second
- Users can now sustain ~100 requests/minute in bursts
- Normal traffic patterns will no longer trigger false rate limiting

### Verification

To verify the fix is working:

1. **Check burst token refill rate**:
   ```python
   burst_limit = 100
   refill_rate = burst_limit / 60  # 1.666 tokens/second
   ```

2. **Monitor deployment logs** for reduction in 429 errors on valid requests

3. **Test with multiple rapid requests** from the same API key:
   ```bash
   for i in {1..50}; do
     curl -X POST https://api.gatewayz.ai/v1/chat/completions \
       -H "Authorization: Bearer $API_KEY" \
       -d '{"model":"gpt-3.5-turbo","messages":[{"role":"user","content":"test"}]}'
   done
   ```
   - Before fix: Would fail after ~10 requests
   - After fix: Should handle all 50 requests without rate limiting

### Related Code

**Rate Limiting Chain**:
1. `src/routes/chat.py:1268` - Calls `rate_limit_mgr.check_rate_limit()`
2. `src/services/rate_limiting.py:597` - Forwards to `SlidingWindowRateLimiter.check_rate_limit()`
3. `src/services/rate_limiting.py:182` - Falls back to `FallbackRateLimitManager.check_rate_limit()`
4. `src/services/rate_limiting_fallback.py:114` - Calls `_check_burst_limit()`
5. `src/services/rate_limiting_fallback.py:198-218` - Burst token bucket implementation

**Config Loading**:
- `src/db/rate_limits.py:372-433` - `get_rate_limit_config()` function
- Returns config with burst_limit (now fixed to 100)

### Prevention

To prevent similar issues in the future:

1. **Add validation** for rate limit configs:
   ```python
   assert burst_limit >= 50, "Burst limit too low for production traffic"
   ```

2. **Add monitoring** for burst token depletion:
   - Alert when burst tokens frequently hit 0
   - Track burst token refill rates

3. **Add integration tests** for burst limiting:
   ```python
   async def test_burst_limit_allows_rapid_requests():
       # Should allow at least 50 rapid requests
       for i in range(50):
           result = await rate_limiter.check_rate_limit(api_key)
           assert result.allowed, f"Request {i} should be allowed"
   ```

4. **Document expected values** in code comments:
   ```python
   # Default burst_limit should be 100 to handle ~100 req/min bursts
   # Minimum recommended: 50
   # Maximum recommended: 250
   ```

### Impact Assessment

**Severity**: HIGH
**Affected Users**: All users making requests to `/v1/chat/completions`
**Duration**: Since deployment with burst_limit=10 configuration
**Mitigation**: Immediate deployment of this fix required

### Deployment Notes

1. **No database migrations required** - This is a code-only fix
2. **No configuration changes needed** - Default values updated in code
3. **Immediate effect** - Fix takes effect on next deployment
4. **No rollback concerns** - Safe to deploy (only increases limits)

### Related Issues

- Railway deployment logs showing repeated 429 errors
- "Rate limit alerts table not available" warnings (unrelated, cosmetic)
- User reports of rate limiting despite low usage

### Testing Performed

1. ✅ Code review of rate limiting chain
2. ✅ Simulation of burst token bucket algorithm
3. ✅ Verification that default configs now have burst_limit=100
4. ✅ Reviewed recent PRs to confirm no conflicts
5. ⏳ Integration testing (pending deployment)

### Follow-up Actions

- [ ] Deploy fix to production
- [ ] Monitor Railway logs for 429 error reduction
- [ ] Add burst limit monitoring dashboard
- [ ] Create integration tests for rate limiting
- [ ] Document rate limit tuning guidelines

---

**Fixed by**: Terry (AI Agent)
**Date**: December 11, 2025
**Branch**: terragon/fix-backend-errors-ovaubi
**Reviewed**: Pending
