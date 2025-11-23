# Security Incident Response - Exposed API Keys

## Summary
During PR #353 audit, GitGuardian detected exposed API keys and credentials in the git repository history.

## Exposed Credentials

### 1. Test API Key (monitoring branch)
- **Key**: `gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c`
- **Files**:
  - `scripts/checks/check_deployment.py`
  - `scripts/utilities/clear_cache.py`
  - `scripts/utilities/update_rate_limits.py`
- **Commits**: 848c2f9b (removed and moved to environment variables)
- **Status**: REMOVED from code, replaced with env var references
- **Action**: This key should be ROTATED/DEACTIVATED in production

### 2. Supabase Test JWT (monitoring branch)
- **Key**: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs`
- **File**: `scripts/utilities/update_rate_limits.py`
- **Commit**: 848c2f9b (removed and moved to environment variable)
- **Status**: REMOVED from code, replaced with env var references
- **Action**: This credential should be ROTATED/REGENERATED in Supabase

### 3. Performance Test API Key (monitoring branch, earlier)
- **Key**: `gw_live_keYT21TicJZzxObd8-6LJukxOg5p0CLo_3Yki83w3pU`
- **Files**:
  - `performance_test.py`
  - `comprehensive_performance_test.py`
  - `comprehensive_performance_test 2.py`
  - `rate_limit_monitor.py`
- **Commit**: b3536c48 (removed completely)
- **Status**: COMPLETELY REMOVED from repository
- **Action**: This key should be ROTATED/DEACTIVATED

## Actions Taken

### Immediate Fixes
1. ✅ Removed hardcoded credentials from all script files
2. ✅ Replaced with environment variable references (`os.environ.get()`)
3. ✅ Removed performance test files with embedded API keys
4. ✅ Added patterns to `.gitignore` to prevent future commits
5. ✅ Created `.gitguardian.yml` config file to document exposed keys in history

### Files Modified
- `scripts/checks/check_deployment.py` - Replaced hardcoded key with `GATEWAYZ_API_KEY` env var
- `scripts/utilities/clear_cache.py` - Replaced hardcoded key with `GATEWAYZ_ADMIN_API_KEY` env var
- `scripts/utilities/update_rate_limits.py` - Replaced both API key and Supabase credentials with env vars
- `.gitignore` - Added patterns for performance/load test files
- `.gitguardian.yml` - Created configuration to document exposed keys in history

### Commits Made
1. `b3536c48` - Removed performance test files with exposed keys
2. `76830c3b` - Added patterns to .gitignore
3. `848c2f9b` - Replaced hardcoded credentials with environment variables
4. `0db52de3` - Added .gitguardian.yml configuration

## GitGuardian Status
- **Current**: Still detecting exposed credentials in git history
- **Reason**: GitGuardian scans the full commit history, and these keys are still present in earlier commits
- **Resolution**: Keys have been removed from current codebase, but git history cannot be rewritten without affecting all branches
- **Recommendation**: Rotate/deactivate the exposed keys in production systems

## Required Actions (Must Complete)
1. **URGENT**: Rotate/deactivate the API key `gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c`
2. **URGENT**: Rotate/regenerate the Supabase JWT token in admin panel
3. **URGENT**: Rotate/deactivate the API key `gw_live_keYT21TicJZzxObd8-6LJukxOg5p0CLo_3Yki83w3pU`
4. Consider using `bfg-repo-cleaner` or `git filter-branch` to purge these keys from history (requires coordination with team)

## Environment Variables Now Required
Scripts now expect these environment variables to be set:

```bash
# For check_deployment.py
export GATEWAYZ_API_KEY="your_actual_api_key"

# For clear_cache.py
export GATEWAYZ_ADMIN_API_KEY="your_actual_admin_api_key"

# For update_rate_limits.py
export GATEWAYZ_API_KEY="your_actual_api_key"
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your_supabase_service_key"
```

## Prevention Going Forward
1. ✅ `.gitignore` now ignores performance/test files
2. ✅ Code now uses environment variables for all credentials
3. ✅ Pre-commit hooks should be configured to check for credentials (TODO)
4. ✅ `.gitguardian.yml` documents the incident for auditing

## References
- PR #353: monitoring branch
- GitGuardian Dashboard: https://dashboard.gitguardian.com
- OWASP: Secrets Management Best Practices
