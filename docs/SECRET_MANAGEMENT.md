# Secret Management & GitGuardian Remediation Guide

## Overview

This guide covers how to properly manage secrets in the Gatewayz backend and remediate any GitGuardian findings.

## GitGuardian Alert Remediation

### Current Issue
- **Alert**: Generic CLI Secret detected in `scripts/create_admin_user.py`
- **Commit**: a35eed6
- **Status**: Needs remediation

### Remediation Steps

#### Step 1: Identify the Secret
```bash
# Check git history for the problematic commit
git show a35eed6

# Look for hardcoded secrets in the file
grep -n "gw_\|sk_\|password\|api_key" scripts/create_admin_user.py
```

#### Step 2: Remove the Secret
```python
# ❌ WRONG - Hardcoded secret
ADMIN_API_KEY = "gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c"

# ✅ CORRECT - Load from environment
import os
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
if not ADMIN_API_KEY:
    raise ValueError("ADMIN_API_KEY environment variable not set")
```

#### Step 3: Update .gitguardian.yml
```yaml
allowlist:
  # Exposed secret from commit a35eed6 (now removed)
  - "EXPOSED_SECRET_HERE"  # Mark as removed/rotated
```

#### Step 4: Rewrite Git History (If Necessary)
```bash
# Option 1: Use git-filter-branch (careful!)
git filter-branch --tree-filter 'sed -i "s/gw_live_01eQv2HGWkjo0ApxoC4-G3yaOv6ilbzJwL9t6QpjQ5c/os.getenv(\"ADMIN_API_KEY\")/g" scripts/create_admin_user.py' -- a35eed6^..HEAD

# Option 2: Use BFG Repo-Cleaner (recommended)
bfg --replace-text secrets.txt

# Option 3: Force push (only if coordinated with team)
git push --force-with-lease
```

#### Step 5: Rotate the Secret
```bash
# 1. Generate new secret
# 2. Update in secure secret management system
# 3. Notify team members
# 4. Update CI/CD pipelines
# 5. Verify old secret is no longer used
```

#### Step 6: Verify Remediation
```bash
# Re-scan with GitGuardian
ggshield secret scan repo .

# Check for any remaining secrets
grep -r "gw_live_\|sk_live_\|password.*=" scripts/ --include="*.py"
```

## Best Practices for Secret Management

### 1. Environment Variables
```python
# ✅ CORRECT
import os
from dotenv import load_dotenv

load_dotenv()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")

if not ADMIN_API_KEY:
    raise ValueError("ADMIN_API_KEY not configured")
```

### 2. Configuration Files
```python
# ✅ CORRECT - Use .env file (never commit)
# .env (in .gitignore)
ADMIN_API_KEY=gw_live_xxxxx
DATABASE_PASSWORD=xxxxx

# ✅ CORRECT - Use config class
from src.config import Config

class Config:
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
    DATABASE_URL = os.getenv("DATABASE_URL")
```

### 3. Secrets Management Systems
```python
# ✅ CORRECT - Use AWS Secrets Manager
import boto3

def get_secret(secret_name):
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=secret_name)
    return response['SecretString']

ADMIN_API_KEY = get_secret('gatewayz/admin-api-key')
```

### 4. Vault Integration
```python
# ✅ CORRECT - Use HashiCorp Vault
import hvac

def get_vault_secret(path, key):
    client = hvac.Client(url='https://vault.example.com')
    secret = client.secrets.kv.read_secret_version(path=path)
    return secret['data']['data'][key]

ADMIN_API_KEY = get_vault_secret('secret/gatewayz', 'admin-api-key')
```

### 5. Docker Secrets
```dockerfile
# ✅ CORRECT - Use Docker secrets
FROM python:3.12

# Don't copy secrets into image
# Use --secret flag at runtime
RUN --mount=type=secret,id=admin_api_key \
    cat /run/secrets/admin_api_key > /app/secrets.txt

# Usage
# docker run --secret admin_api_key=<secret> image:tag
```

## Pre-commit Hook Setup

### Installation
```bash
# Install pre-commit
pip install pre-commit

# Install the git hooks
pre-commit install

# Run against all files
pre-commit run --all-files
```

### Configuration
The `.pre-commit-config.yaml` includes:
- **ggshield**: GitGuardian secret detection
- **detect-secrets**: Credential detection
- **private-key-detector**: SSH key detection
- **trailing-whitespace**: Code quality
- **black**: Code formatting
- **isort**: Import sorting

### Running Pre-commit
```bash
# Run on staged files
pre-commit run

# Run on all files
pre-commit run --all-files

# Run specific hook
pre-commit run ggshield --all-files

# Skip pre-commit (not recommended)
git commit --no-verify
```

## Environment Variables Setup

### Development (.env)
```bash
# .env (never commit)
ADMIN_API_KEY=gw_test_xxxxx
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://user:pass@localhost/db
STRIPE_SECRET_KEY=sk_test_xxxxx
```

### Staging (.env.staging)
```bash
# .env.staging (in .gitignore)
ADMIN_API_KEY=gw_staging_xxxxx
REDIS_URL=redis://staging-redis:6379
DATABASE_URL=postgresql://staging-user:pass@staging-db/db
STRIPE_SECRET_KEY=sk_test_xxxxx
```

### Production (Environment Variables)
```bash
# Set via CI/CD or deployment platform
export ADMIN_API_KEY=gw_live_xxxxx
export REDIS_URL=redis://prod-redis:6379
export DATABASE_URL=postgresql://prod-user:pass@prod-db/db
export STRIPE_SECRET_KEY=sk_live_xxxxx
```

## Detecting Secrets in Code

### Using detect-secrets
```bash
# Scan repository
detect-secrets scan

# Scan specific file
detect-secrets scan scripts/create_admin_user.py

# Audit findings
detect-secrets audit .secrets.baseline
```

### Using ggshield
```bash
# Scan current directory
ggshield secret scan repo .

# Scan specific file
ggshield secret scan repo scripts/create_admin_user.py

# Scan with verbose output
ggshield secret scan repo . --verbose

# Check for specific secret type
ggshield secret scan repo . --type "Generic CLI Secret"
```

### Using grep
```bash
# Search for common patterns
grep -r "gw_live_\|sk_live_\|password.*=\|api_key.*=" . \
  --include="*.py" \
  --exclude-dir=.git \
  --exclude-dir=__pycache__

# Search for specific secret
grep -r "a35eed6\|EXPOSED_SECRET" . --include="*.py"
```

## Handling Exposed Secrets

### Immediate Actions
1. **Identify** all exposed secrets
2. **Revoke** the exposed credentials
3. **Rotate** to new credentials
4. **Remove** from codebase
5. **Rewrite** git history (if necessary)
6. **Notify** team and stakeholders

### Revocation Checklist
- [ ] Identify all exposed secrets
- [ ] Check where secrets are used
- [ ] Generate new credentials
- [ ] Update all references
- [ ] Revoke old credentials
- [ ] Verify old credentials don't work
- [ ] Update CI/CD pipelines
- [ ] Notify team members
- [ ] Document incident

### Example: Rotating API Key
```bash
# 1. Generate new key
NEW_KEY=$(openssl rand -hex 32)
echo "gw_live_$NEW_KEY"

# 2. Update environment variables
export ADMIN_API_KEY="gw_live_$NEW_KEY"

# 3. Update in secret management system
aws secretsmanager update-secret \
  --secret-id gatewayz/admin-api-key \
  --secret-string "gw_live_$NEW_KEY"

# 4. Verify new key works
curl -H "Authorization: Bearer gw_live_$NEW_KEY" \
  https://api.gatewayz.ai/health

# 5. Revoke old key
aws secretsmanager delete-secret \
  --secret-id gatewayz/admin-api-key-old \
  --force-delete-without-recovery

# 6. Update git history
git filter-branch --tree-filter \
  'sed -i "s/gw_live_OLD/gw_live_NEW/g" scripts/*.py' \
  -- HEAD~10..HEAD
```

## CI/CD Secret Management

### GitHub Actions
```yaml
# .github/workflows/deploy.yml
name: Deploy

on: [push]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      # Use GitHub Secrets
      - name: Deploy
        env:
          ADMIN_API_KEY: ${{ secrets.ADMIN_API_KEY }}
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: |
          python scripts/deploy.py
```

### GitLab CI
```yaml
# .gitlab-ci.yml
deploy:
  stage: deploy
  script:
    - python scripts/deploy.py
  variables:
    ADMIN_API_KEY: $ADMIN_API_KEY
    DATABASE_URL: $DATABASE_URL
```

### Railway
```yaml
# railway.json
{
  "variables": {
    "ADMIN_API_KEY": "$ADMIN_API_KEY",
    "DATABASE_URL": "$DATABASE_URL"
  }
}
```

## Monitoring & Alerts

### Set Up Alerts
```bash
# Monitor for secret exposure
ggshield secret scan repo . --json > scan-results.json

# Alert if secrets found
if grep -q '"type": "Generic CLI Secret"' scan-results.json; then
  echo "ALERT: Secrets detected in repository!"
  exit 1
fi
```

### Regular Audits
```bash
# Weekly secret scan
0 0 * * 0 ggshield secret scan repo /path/to/repo

# Monthly credential rotation
0 0 1 * * /scripts/rotate-credentials.sh
```

## Troubleshooting

### Pre-commit Hook Issues
```bash
# Bypass pre-commit (not recommended)
git commit --no-verify

# Reinstall hooks
pre-commit install --install-hooks

# Update hooks
pre-commit autoupdate

# Debug hook
pre-commit run ggshield --all-files --verbose
```

### False Positives
```yaml
# .gitguardian.yml - Allowlist false positives
allowlist:
  # Test secret that's safe to expose
  - "test_key_12345"
  # Example from documentation
  - "example_api_key_xyz"
```

### Secret Not Detected
```bash
# Verify ggshield is installed
ggshield --version

# Update ggshield
pip install --upgrade ggshield

# Check configuration
cat .gitguardian.yml

# Run with verbose output
ggshield secret scan repo . --verbose
```

## References

- [GitGuardian Documentation](https://docs.gitguardian.com/)
- [detect-secrets](https://github.com/Yelp/detect-secrets)
- [pre-commit Framework](https://pre-commit.com/)
- [OWASP Secret Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [12 Factor App - Config](https://12factor.net/config)

## Quick Reference

### Don't Do This ❌
```python
ADMIN_API_KEY = "gw_live_xxxxx"
DATABASE_PASSWORD = "password123"
STRIPE_KEY = "sk_live_xxxxx"
```

### Do This Instead ✅
```python
import os
from dotenv import load_dotenv

load_dotenv()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
STRIPE_KEY = os.getenv("STRIPE_KEY")
```

### Commit This ✅
```
.env
.env.local
.env.*.local
secrets.json
credentials.json
```

### Never Commit This ❌
```
API keys
Passwords
Database credentials
Private keys
OAuth tokens
```
