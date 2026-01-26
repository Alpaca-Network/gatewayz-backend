# Production Verification Scripts

Scripts for verifying production readiness before pricing scheduler deployment.

## Scripts Overview

### 1. verify_production_readiness.py

**Purpose**: Automated production readiness verification (read-only)

**Usage**:
```bash
# With admin key (recommended)
export PROD_ADMIN_KEY="your_production_admin_key"
python3 scripts/verify_production_readiness.py

# With admin key as argument
python3 scripts/verify_production_readiness.py --admin-key "your_key"

# Without admin key (limited checks)
python3 scripts/verify_production_readiness.py

# Custom production URL
python3 scripts/verify_production_readiness.py --production-url "https://custom.api.com"
```

**Checks Performed**:
- ✅ Production health endpoint
- ✅ Admin endpoint availability
- ✅ Metrics endpoint validation
- 📋 Database schema verification guide
- 📋 Configuration validation guide

**Output**:
- Console output with colored status indicators
- JSON report: `production_verification_report_YYYYMMDD_HHMMSS.json`

**Exit Codes**:
- `0`: All checks passed
- `1`: One or more checks failed

---

### 2. get_production_admin_key.py

**Purpose**: Retrieve production admin API key from database

**Prerequisites**:
- Production Supabase URL
- Production Supabase service role key

**Usage**:
```bash
# Set production credentials
export SUPABASE_URL="https://your-production-instance.supabase.co"
export SUPABASE_KEY="your_production_service_role_key"

# Run script
python3 scripts/get_production_admin_key.py
```

**Output**:
- Displays all admin users and their API keys
- Saves first admin key to `.admin_key_production`

**Security**: Ensure `.admin_key_production` is in `.gitignore`

---

### 3. get_admin_api_key.py

**Purpose**: Retrieve staging admin API key

**Usage**:
```bash
python3 scripts/get_admin_api_key.py
```

**Output**:
- Saves admin key to `.admin_key_staging`

---

## Quick Start Workflow

### Full Production Verification

```bash
# Step 1: Get production admin key
export SUPABASE_URL="https://your-production-instance.supabase.co"
export SUPABASE_KEY="your_production_service_role_key"
python3 scripts/get_production_admin_key.py

# Step 2: Run verification
export PROD_ADMIN_KEY=$(cat .admin_key_production)
python3 scripts/verify_production_readiness.py

# Step 3: Review report
cat production_verification_report_*.json | jq '.summary'
```

### Staging Verification

```bash
# Get staging admin key
python3 scripts/get_admin_api_key.py

# Run tests against staging
export ADMIN_KEY=$(cat .admin_key_staging)
# Run your staging tests...
```

## Verification Report Format

The verification script generates a JSON report with this structure:

```json
{
  "verification_timestamp": "2026-01-26T20:00:00+00:00",
  "production_url": "https://api.gatewayz.ai",
  "summary": {
    "total_automated_checks": 5,
    "passed": 5,
    "failed": 0,
    "manual_checks": 2
  },
  "checks": [
    {
      "step": "3",
      "check": "Production health endpoint",
      "passed": true,
      "details": "Status: healthy, Database: connected",
      "timestamp": "2026-01-26T20:00:00+00:00"
    }
    // ... more checks
  ],
  "ready_for_deployment": true
}
```

## Common Issues

### "No admin key provided"

**Solution**: Export `PROD_ADMIN_KEY` environment variable or use `--admin-key` argument

```bash
export PROD_ADMIN_KEY=$(cat .admin_key_production)
python3 scripts/verify_production_readiness.py
```

### "SUPABASE_KEY environment variable not set"

**Solution**: Export production Supabase credentials before running `get_production_admin_key.py`

```bash
export SUPABASE_URL="https://your-instance.supabase.co"
export SUPABASE_KEY="your_service_role_key"
```

### Certificate/SSL Errors

**Solution**: Ensure you're using HTTPS URLs and have valid SSL certificates

### Connection Timeout

**Solution**: Check network connectivity to production, increase timeout if needed

## Security Best Practices

1. **Never commit API keys**: Ensure `.admin_key_*` files are in `.gitignore`
2. **Use environment variables**: Don't hardcode credentials in scripts
3. **Read-only verification**: Scripts only perform read operations
4. **Audit logs**: Review audit logs after verification
5. **Secure transmission**: Always use HTTPS for production APIs

## Integration with CI/CD

These scripts can be integrated into deployment pipelines:

```yaml
# Example GitHub Actions workflow
- name: Verify Production Readiness
  env:
    PROD_ADMIN_KEY: ${{ secrets.PROD_ADMIN_KEY }}
  run: |
    python3 scripts/verify_production_readiness.py

- name: Check Verification Report
  run: |
    READY=$(cat production_verification_report_*.json | jq -r '.ready_for_deployment')
    if [ "$READY" != "true" ]; then
      echo "Production not ready for deployment"
      exit 1
    fi
```

## Related Documentation

- **Full Verification Guide**: `../docs/PRODUCTION_VERIFICATION_GUIDE.md`
- **Quick Checklist**: `../PRODUCTION_VERIFICATION_CHECKLIST.md`
- **Deployment Guide**: `../docs/PHASE_5_DEPLOYMENT_GUIDE.md`
- **Rollback Procedure**: `../docs/ROLLBACK_PROCEDURE.md`

## Support

For issues or questions:
1. Check troubleshooting section in `PRODUCTION_VERIFICATION_GUIDE.md`
2. Review script source code for detailed implementation
3. Contact DevOps team
4. Refer to GitHub issue #960

---

**Last Updated**: 2026-01-26
**Issue**: #960
