# GitHub Actions Secrets Fix

## 🐛 Problem

When running the Supabase migrations workflow, you saw these warnings:

```
##[warning]Skip output 'environment' since it may contain secret.
##[warning]Skip output 'supabase_url' since it may contain secret.
##[warning]Skip output 'supabase_key' since it may contain secret.
##[warning]Skip output 'project_ref' since it may contain secret.
##[warning]Skip output 'db_password' since it may contain secret.
```

**Root Cause**: The workflow was trying to pass **secret values** through job outputs, which GitHub Actions blocks for security reasons.

---

## ✅ Solution Applied

### **Before (Incorrect):**
```yaml
setup-environment:
  outputs:
    environment: ${{ steps.set-env.outputs.environment }}
    supabase_url: ${{ steps.set-env.outputs.supabase_url }}      # ❌ Secret!
    supabase_key: ${{ steps.set-env.outputs.supabase_key }}      # ❌ Secret!
    project_ref: ${{ steps.set-env.outputs.project_ref }}        # ❌ Secret!
    db_password: ${{ steps.set-env.outputs.db_password }}        # ❌ Secret!

  steps:
    - run: |
        echo "supabase_url=${{ secrets.SUPABASE_URL }}" >> $GITHUB_OUTPUT  # ❌ Can't do this!
```

### **After (Correct):**
```yaml
setup-environment:
  outputs:
    environment: ${{ steps.set-env.outputs.environment }}  # ✅ Only non-sensitive data

  steps:
    - run: |
        echo "environment=$ENV" >> $GITHUB_OUTPUT  # ✅ Just the environment name

# Other jobs access secrets directly:
apply-migrations:
  steps:
    - name: Link to Supabase project
      env:
        ENVIRONMENT: ${{ needs.setup-environment.outputs.environment }}
        # Access secrets directly based on environment
        PROD_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}
        PROD_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
        STAGING_PROJECT_REF: ${{ secrets.SUPABASE_STAGING_PROJECT_REF }}
        STAGING_DB_PASSWORD: ${{ secrets.SUPABASE_STAGING_DB_PASSWORD }}
      run: |
        # Select credentials based on environment
        if [ "$ENVIRONMENT" == "production" ]; then
          PROJECT_REF="$PROD_PROJECT_REF"
          DB_PASSWORD="$PROD_DB_PASSWORD"
        else
          PROJECT_REF="$STAGING_PROJECT_REF"
          DB_PASSWORD="$STAGING_DB_PASSWORD"
        fi

        # Use the credentials
        supabase link --project-ref "$PROJECT_REF" --password "$DB_PASSWORD"
```

---

## 🔑 Key Changes

1. **Removed secret outputs** from `setup-environment` job
2. **Kept only non-sensitive output**: `environment` (e.g., "production" or "staging")
3. **Access secrets directly** in jobs that need them
4. **Use environment variable** to select which secrets to use

---

## 📊 Comparison

| Approach | Job Outputs | Secrets Access | Security |
|----------|-------------|----------------|----------|
| **Before (Wrong)** | Pass secrets through outputs | Centralized in setup job | ❌ Blocked by GitHub |
| **After (Correct)** | Pass only environment name | Each job accesses directly | ✅ Secure & Works |

---

## 🔒 Why This Is More Secure

1. **No secret exposure**: Secrets never appear in job outputs or logs
2. **GitHub auto-redaction**: Secrets are automatically masked in logs
3. **Principle of least privilege**: Each job only accesses the secrets it needs
4. **Standard pattern**: This is the recommended GitHub Actions pattern

---

## 🎯 Benefits

✅ **No more warnings** about skipped outputs
✅ **Secrets remain protected** and never exposed
✅ **Workflow runs successfully** without security blocks
✅ **Follows GitHub Actions best practices**
✅ **More maintainable** and easier to understand

---

## 📝 How It Works Now

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ setup-environment Job                                           │
│ • Determines environment: "production" or "staging"             │
│ • Outputs: environment name (non-sensitive)                     │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ apply-migrations Job                                            │
│ • Reads environment name from setup-environment output          │
│ • Accesses secrets directly based on environment:               │
│   - If "production": uses SUPABASE_PROJECT_REF, etc.           │
│   - If "staging": uses SUPABASE_STAGING_PROJECT_REF, etc.      │
│ • Applies migrations using selected credentials                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🧪 Testing

After this fix, the workflow should:
1. ✅ Not show any warnings about skipped outputs
2. ✅ Successfully authenticate with Supabase
3. ✅ Link to the correct project (production or staging)
4. ✅ Apply migrations successfully

**Test command:**
```bash
gh workflow run supabase-migrations.yml \
  --field environment=staging \
  --field dry_run=true
```

---

## 📚 Related Documentation

- [GitHub Actions: Encrypted Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [GitHub Actions: Using secrets in a workflow](https://docs.github.com/en/actions/security-guides/encrypted-secrets#using-encrypted-secrets-in-a-workflow)
- [Supabase Migrations CI Guide](SUPABASE_MIGRATIONS_CI.md)
- [GitHub Secrets Setup Guide](GITHUB_SECRETS_SETUP.md)

---

## 💡 Key Takeaway

**Never pass secrets through job outputs!**

✅ **DO**: Access secrets directly in environment variables
❌ **DON'T**: Try to pass secrets through job outputs

This is a fundamental security pattern in GitHub Actions.
