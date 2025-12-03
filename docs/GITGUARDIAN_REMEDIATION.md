# GitGuardian Alert #22799369 - Remediation Steps

## Alert Summary
- **Alert ID**: 22799369
- **Status**: Triggered
- **Type**: Generic CLI Secret
- **File**: `scripts/create_admin_user.py`
- **Commit**: a35eed6
- **Affected PR**: #163

## Root Cause
A hardcoded secret (likely an API key or authentication token) was committed to the repository in the `create_admin_user.py` script.

## Immediate Remediation

### Step 1: Verify Current State
```bash
# Check if file exists in current branch
ls -la scripts/create_admin_user.py

# Check git history for the file
git log --oneline scripts/create_admin_user.py

# Show the problematic commit
git show a35eed6
```

### Step 2: Remove Hardcoded Secret
If the file exists, update it to use environment variables:

```python
# ❌ BEFORE (Hardcoded - WRONG)
ADMIN_API_KEY = "gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c"

# ✅ AFTER (Environment Variable - CORRECT)
import os
from dotenv import load_dotenv

load_dotenv()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
if not ADMIN_API_KEY:
    raise ValueError("ADMIN_API_KEY environment variable must be set")
```

### Step 3: Update .gitguardian.yml
Add the exposed secret to the allowlist (marking it as known/rotated):

```yaml
allowlist:
  # Exposed secret from commit a35eed6 (now removed and rotated)
  - "gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c"
```

### Step 4: Commit Changes
```bash
# Stage changes
git add scripts/create_admin_user.py .gitguardian.yml

# Commit with clear message
git commit -m "fix: remove hardcoded secret from create_admin_user.py

- Replaced hardcoded ADMIN_API_KEY with environment variable
- Updated .gitguardian.yml to allowlist rotated secret
- Resolves GitGuardian alert #22799369"

# Push changes
git push origin your-branch
```

### Step 5: Rotate the Exposed Secret
```bash
# 1. Generate new secret
NEW_SECRET=$(openssl rand -hex 32)
echo "gw_live_$NEW_SECRET"

# 2. Update in your secret management system
# - Update .env file (local development)
# - Update CI/CD secrets (GitHub Actions, GitLab CI, etc.)
# - Update deployment platform (Railway, Vercel, etc.)
# - Update any other systems using this key

# 3. Verify new secret works
curl -H "Authorization: Bearer gw_live_$NEW_SECRET" \
  https://api.gatewayz.ai/health

# 4. Revoke old secret
# - Delete from secret management system
# - Notify team members
# - Update documentation
```

### Step 6: Verify Remediation
```bash
# Re-scan with ggshield
ggshield secret scan repo .

# Check for remaining secrets
grep -r "gw_live_\|sk_live_\|password.*=" scripts/ --include="*.py"

# Verify pre-commit hook works
pre-commit run --all-files
```

## Long-Term Prevention

### 1. Install Pre-commit Hook
```bash
# Install pre-commit framework
pip install pre-commit

# Install git hooks
pre-commit install

# Run on all files
pre-commit run --all-files
```

### 2. Configure Environment Variables
Create `.env` file (in `.gitignore`):
```bash
ADMIN_API_KEY=gw_live_xxxxx
DATABASE_URL=postgresql://user:pass@localhost/db
STRIPE_SECRET_KEY=sk_test_xxxxx
```

### 3. Update Scripts
All scripts should load from environment:
```python
import os
from dotenv import load_dotenv

load_dotenv()

# Load secrets from environment
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
STRIPE_KEY = os.getenv("STRIPE_KEY")

# Validate required secrets
required_secrets = ["ADMIN_API_KEY", "DATABASE_URL"]
missing = [s for s in required_secrets if not os.getenv(s)]
if missing:
    raise ValueError(f"Missing required environment variables: {missing}")
```

### 4. CI/CD Configuration
Update GitHub Actions, GitLab CI, or Railway to use secrets:

**GitHub Actions**:
```yaml
- name: Run Script
  env:
    ADMIN_API_KEY: ${{ secrets.ADMIN_API_KEY }}
  run: python scripts/create_admin_user.py
```

**GitLab CI**:
```yaml
script:
  - python scripts/create_admin_user.py
variables:
  ADMIN_API_KEY: $ADMIN_API_KEY
```

**Railway**:
```json
{
  "variables": {
    "ADMIN_API_KEY": "$ADMIN_API_KEY"
  }
}
```

## Verification Checklist

- [ ] Hardcoded secret removed from code
- [ ] Code updated to use environment variables
- [ ] `.gitguardian.yml` updated with allowlist
- [ ] Changes committed and pushed
- [ ] Old secret rotated/revoked
- [ ] New secret deployed to all environments
- [ ] Pre-commit hook installed locally
- [ ] Team notified of changes
- [ ] Documentation updated
- [ ] ggshield re-scan passes
- [ ] No remaining hardcoded secrets found

## Testing

### Local Testing
```bash
# Test with environment variable set
export ADMIN_API_KEY="gw_live_test_xxxxx"
python scripts/create_admin_user.py

# Test with missing environment variable
unset ADMIN_API_KEY
python scripts/create_admin_user.py  # Should raise error
```

### CI/CD Testing
```bash
# Run ggshield in CI
ggshield secret scan repo . --exit-code

# Run detect-secrets in CI
detect-secrets scan --baseline .secrets.baseline

# Fail if secrets detected
if [ $? -ne 0 ]; then
  echo "Secrets detected!"
  exit 1
fi
```

## Rollback Plan

If issues occur after remediation:

```bash
# 1. Identify the issue
git log --oneline -10

# 2. Revert the commit
git revert HEAD

# 3. Push revert
git push origin your-branch

# 4. Investigate root cause
# 5. Fix and re-commit
```

## References

- **GitGuardian Docs**: https://docs.gitguardian.com/
- **Secret Management Guide**: `docs/SECRET_MANAGEMENT.md`
- **Pre-commit Config**: `.pre-commit-config.yaml`
- **Environment Setup**: `.env.example`

## Support

For questions or issues:
1. Review `docs/SECRET_MANAGEMENT.md`
2. Check `.pre-commit-config.yaml` configuration
3. Run `ggshield secret scan repo . --verbose`
4. Contact security team

---

**Last Updated**: 2025-01-15
**Status**: Ready for remediation
**Priority**: High (Security)
