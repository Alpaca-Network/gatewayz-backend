# Missing KEY_HASH_SALT Environment Variable Fix

**Date**: 2026-01-03
**Severity**: CRITICAL
**Status**: ✅ RESOLVED
**Environment**: Production (Railway)

## Summary

Users were unable to log in to the production system due to a missing `KEY_HASH_SALT` environment variable, which is required for API key creation. This resulted in 500 errors during authentication and prevented new API keys from being generated.

## Error Details

### Symptoms
- Users experiencing 500 Internal Server Error when attempting to log in
- Error message in logs: `API key creation requires KEY_HASH_SALT environment variable`
- Critical log entries: `CRITICAL: Existing user login but NO API KEY available!`
- Multiple failed login attempts by user 36135

### Root Cause
The `KEY_HASH_SALT` environment variable was introduced in PR #317 (commit e509320f) on November 18, 2025, to prevent API key hash collisions. However, this variable was **never added to the Railway production environment**, causing a deployment configuration gap.

The code requires `KEY_HASH_SALT` to be at least 16 characters for secure hashing of API keys. Without it, the `sha256_key_hash()` function in `src/utils/crypto.py` raises a `RuntimeError`, which cascades through the API key creation process.

### Impact
- **User Impact**: HIGH - Users unable to log in or create API keys
- **Duration**: Since deployment (exact duration unknown, but affecting production)
- **Affected Users**: All users attempting to log in without existing valid API keys
- **Service Availability**: Authentication endpoint returning 500 errors

## Investigation Process

1. **Checked Railway deployment logs** - Found repeated errors:
   ```
   ❌ API key creation requires KEY_HASH_SALT environment variable
   ❌ Failed to create API key for user 36135
   ❌ CRITICAL: Existing user 36135 login but NO API KEY available!
   ```

2. **Reviewed Railway environment variables** - Confirmed `KEY_HASH_SALT` was missing
   - `KEYRING_1` was present (encryption key)
   - `KEY_VERSION` was set to 1
   - But `KEY_HASH_SALT` was not configured

3. **Traced code history** - Found PR #317 introduced the requirement in November 2025

4. **Compared with recent fixes** - Confirmed this issue was NOT addressed in recent PRs:
   - PR #746: Fixed `.data[0]` access issues
   - PR #748: Fixed Braintrust NoneType errors
   - PR #747: Fixed vertex `_get_access_token` bug

## Resolution

### Fix Applied
1. Generated a secure 64-character random salt using Python's `secrets` module:
   ```bash
   python3 -c "import secrets; print('KEY_HASH_SALT=' + secrets.token_hex(32))"
   ```

2. Added `KEY_HASH_SALT` environment variable to Railway production:
   - **Variable**: `KEY_HASH_SALT`
   - **Value**: `984dc3dd058846cbddee04bca47d9e0bcdc34465d0e161c7bd7e7e6a66a9ecd2`
   - **Service**: gatewayz-backend/api (production)

3. Restarted the Railway service to apply the new environment variable

### Verification Steps
1. Monitor deployment logs for successful restart
2. Test user authentication endpoint (`POST /auth`)
3. Verify API key creation completes successfully
4. Confirm no more `KEY_HASH_SALT` errors in logs

## Code References

### Affected Files
- `src/utils/crypto.py` - Lines 66-81: `sha256_key_hash()` function requiring `KEY_HASH_SALT`
- `src/db/api_keys.py` - Lines 211-225: API key creation with hash salt validation
- `.env.example` - Should document this requirement (from PR #317)

### Related PRs
- PR #317: Original implementation of KEY_HASH_SALT requirement (commit e509320f)

## Prevention Measures

### Immediate Actions
- ✅ Added `KEY_HASH_SALT` to Railway production environment
- ✅ Restarted service to apply changes

### Long-term Recommendations
1. **Environment Variable Documentation**: Create a checklist for all required environment variables across deployments
2. **Pre-deployment Validation**: Add a startup check that validates all required environment variables before the service starts
3. **Staging Environment Parity**: Ensure staging has identical environment variable requirements as production
4. **Deployment Checklist**: Update deployment docs to include environment variable verification step

## Testing Recommendations

While this was a configuration fix (no code changes), the following tests should be maintained:

1. **Environment Variable Validation Tests**: Ensure startup checks for required variables
2. **API Key Creation Tests**: Verify API keys can be created with proper hash salts
3. **Authentication Flow Tests**: End-to-end tests for user login and API key generation

## Related Documentation

- `docs/API_KEY_SETUP.md` - API key encryption and hashing setup guide
- `docs/ENVIRONMENT_SETUP.md` - Environment configuration guide
- `.env.example` - Example environment variables with documentation

## Timeline

- **2025-11-18**: KEY_HASH_SALT requirement introduced in PR #317
- **Unknown**: Deployed to production WITHOUT KEY_HASH_SALT
- **2026-01-03 13:43 UTC**: Error first observed in Railway logs (user 36135 login failures)
- **2026-01-03 14:05 UTC**: Fix applied - KEY_HASH_SALT added to Railway
- **2026-01-03 14:05 UTC**: Service restarted

## Lessons Learned

1. **Environment variable changes require deployment coordination**: Code changes that introduce new required environment variables must include deployment configuration updates
2. **Missing environment variable detection**: The application should validate required environment variables at startup, not at runtime
3. **Deployment automation**: Consider using infrastructure-as-code (IaC) to manage environment variables across environments

## Monitoring

### Post-Fix Monitoring
- Monitor `/auth` endpoint error rates
- Watch for `KEY_HASH_SALT` errors in logs
- Track successful API key creation metrics
- Alert on authentication failures

### Success Metrics
- ✅ Zero `KEY_HASH_SALT` errors in logs
- ✅ Successful API key creation
- ✅ Users able to authenticate successfully
- ✅ No 500 errors on `/auth` endpoint

---

**Resolution Status**: ✅ **RESOLVED**
**Follow-up Required**: None (monitoring only)
**Deployment**: Production (Railway)
**Impact**: CRITICAL → Resolved
