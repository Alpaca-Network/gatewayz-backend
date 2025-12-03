# Secrets Management Quick Reference Card

## 🚨 If You Accidentally Commit a Secret

```bash
# 1. STOP - Don't push yet
git reset HEAD~1

# 2. Remove the secret
# Edit the file and remove hardcoded secret

# 3. Use environment variable instead
export SECRET_NAME="your_secret_value"

# 4. Commit again
git add .
git commit -m "fix: use environment variable for secret"

# 5. Rotate the secret immediately
# - Generate new secret
# - Update all systems
# - Revoke old secret

# 6. Add to .gitguardian.yml allowlist
# - Mark as rotated
# - Document commit hash
```

## ✅ Do's and Don'ts

### ❌ DON'T
```python
# Hardcoded secrets
API_KEY = "sk_live_xxxxx"
PASSWORD = "secret123"
DATABASE_URL = "postgresql://user:pass@host/db"

# Secrets in config files
config = {
    "api_key": "gw_live_xxxxx",
    "secret": "my_secret"
}

# Secrets in comments
# ADMIN_KEY = "gw_live_xxxxx"  # Don't use this anymore
```

### ✅ DO
```python
# Environment variables
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
PASSWORD = os.getenv("PASSWORD")
DATABASE_URL = os.getenv("DATABASE_URL")

# With validation
if not API_KEY:
    raise ValueError("API_KEY environment variable not set")

# In .env file (never commit)
# API_KEY=sk_live_xxxxx
# PASSWORD=secret123
```

## 🔧 Setup Pre-commit Hook

```bash
# 1. Install pre-commit
pip install pre-commit

# 2. Install hooks
pre-commit install

# 3. Run on all files
pre-commit run --all-files

# 4. Commit changes
git add .
git commit -m "chore: install pre-commit hooks"
```

## 🔍 Scan for Secrets

```bash
# Using ggshield
ggshield secret scan repo .

# Using detect-secrets
detect-secrets scan

# Using grep (quick check)
grep -r "password\|api_key\|secret" . \
  --include="*.py" \
  --exclude-dir=.git
```

## 📝 Environment Variables

### Development (.env)
```bash
# .env (in .gitignore)
API_KEY=sk_test_xxxxx
DATABASE_URL=postgresql://user:pass@localhost/db
ADMIN_KEY=gw_test_xxxxx
```

### Staging
```bash
# Set via CI/CD or deployment platform
export API_KEY=sk_test_staging_xxxxx
export DATABASE_URL=postgresql://staging-user:pass@staging-db/db
export ADMIN_KEY=gw_staging_xxxxx
```

### Production
```bash
# Set via secure secret management
export API_KEY=sk_live_xxxxx
export DATABASE_URL=postgresql://prod-user:pass@prod-db/db
export ADMIN_KEY=gw_live_xxxxx
```

## 🚀 CI/CD Integration

### GitHub Actions
```yaml
- name: Run Script
  env:
    API_KEY: ${{ secrets.API_KEY }}
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
  run: python script.py
```

### GitLab CI
```yaml
script:
  - python script.py
variables:
  API_KEY: $API_KEY
  DATABASE_URL: $DATABASE_URL
```

### Railway
```json
{
  "variables": {
    "API_KEY": "$API_KEY",
    "DATABASE_URL": "$DATABASE_URL"
  }
}
```

## 🔐 Secret Types

| Type | Example | Storage |
|------|---------|---------|
| API Key | `sk_live_xxxxx` | Environment variable |
| Database Password | `postgres_pass` | Secret manager |
| OAuth Token | `ghp_xxxxx` | Vault/Secrets Manager |
| SSH Key | `-----BEGIN PRIVATE KEY-----` | SSH agent |
| JWT Secret | `base64_encoded_secret` | Environment variable |
| API Secret | `gw_live_xxxxx` | Secret manager |

## 📋 Checklist Before Commit

- [ ] No hardcoded API keys
- [ ] No hardcoded passwords
- [ ] No hardcoded database URLs
- [ ] No hardcoded OAuth tokens
- [ ] No hardcoded SSH keys
- [ ] All secrets use environment variables
- [ ] Pre-commit hook passes
- [ ] No secrets in comments
- [ ] No secrets in config files
- [ ] .env is in .gitignore

## 🆘 Emergency: Secret Exposed

```bash
# 1. IMMEDIATELY revoke the secret
# Contact the service provider
# Generate new secret

# 2. Update all references
export NEW_SECRET="new_value"

# 3. Remove from git history
git filter-branch --tree-filter 'sed -i "s/OLD_SECRET/NEW_SECRET/g" *' -- HEAD~10..HEAD

# 4. Force push (coordinate with team)
git push --force-with-lease

# 5. Notify team
# - Send incident report
# - Share remediation steps
# - Update documentation

# 6. Update .gitguardian.yml
# Add exposed secret to allowlist
```

## 📚 Documentation

- **Full Guide**: `docs/SECRET_MANAGEMENT.md`
- **Remediation**: `docs/GITGUARDIAN_REMEDIATION.md`
- **Action Plan**: `docs/GITGUARDIAN_ACTION_PLAN.md`
- **Summary**: `docs/GITGUARDIAN_SUMMARY.md`

## 🔗 Useful Commands

```bash
# Check if secret is in git history
git log -p --all -S "secret_value"

# Find all env variables used
grep -r "os.getenv\|os.environ" . --include="*.py"

# Check .env file
cat .env

# Verify secret is set
echo $API_KEY

# Test with secret
curl -H "Authorization: Bearer $API_KEY" https://api.example.com

# Rotate secret
# 1. Generate new
# 2. Update environment
# 3. Test new secret
# 4. Revoke old secret
```

## 💡 Best Practices

1. **Never commit secrets** - Use environment variables
2. **Use .gitignore** - Add `.env` and similar files
3. **Install pre-commit** - Catch secrets before commit
4. **Rotate regularly** - Change secrets periodically
5. **Use secret manager** - For production secrets
6. **Document procedures** - Train team on best practices
7. **Monitor access** - Track who accesses secrets
8. **Audit logs** - Keep records of secret usage

## 🎓 Team Training

All team members should:
- [ ] Read `docs/SECRET_MANAGEMENT.md`
- [ ] Install pre-commit hooks
- [ ] Configure IDE/editor
- [ ] Understand secret rotation
- [ ] Know incident response
- [ ] Review this quick reference

## 📞 Support

**Questions?**
1. Check `docs/SECRET_MANAGEMENT.md`
2. Review `.pre-commit-config.yaml`
3. Run `ggshield secret scan repo . --verbose`
4. Contact security team

**Found a secret?**
1. Don't panic
2. Follow "Emergency: Secret Exposed" steps
3. Notify security team immediately
4. Document the incident

---

**Last Updated**: 2025-01-15
**Priority**: High
**Status**: Active
