# GitHub Linting Integration

This document describes the GitHub Actions workflows for automated linting.

## 🎯 Overview

We have **3 GitHub workflows** for linting:

1. **`lint.yml`** - Main linting checks (runs on push/PR)
2. **`lint-autofix.yml`** - Auto-fix workflow (manual/scheduled)
3. **`lint-pr-review.yml`** - PR inline comments with ReviewDog

---

## 🔍 1. Main Linting Workflow (`lint.yml`)

**Triggers:**
- Every push to `main` or `develop`
- Every pull request to `main` or `develop`

**What it does:**
- ✅ Runs Ruff linter
- ✅ Checks Black formatting
- ✅ Checks import sorting (isort)
- ✅ Runs MyPy type checking (warning only)

**Status:**
- ✅ **PASS** - All checks pass, PR can be merged
- ❌ **FAIL** - Linting issues found, needs fixes

### View Status

Add this badge to your README.md:

```markdown
[![Lint](https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/lint.yml/badge.svg)](https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/lint.yml)
```

---

## 🤖 2. Auto-fix Workflow (`lint-autofix.yml`)

**Triggers:**
- Manual dispatch (Actions tab → "Auto-fix Linting Issues" → Run workflow)
- Weekly schedule (Sundays at midnight)

**What it does:**
1. Runs all auto-fixers (ruff, black, isort)
2. Creates a PR with the fixes
3. Labels it as `automated` and `linting`

**When to use:**
- After major refactoring
- Before releases
- To clean up accumulated linting debt

### How to Trigger Manually

1. Go to **Actions** tab
2. Click **"Auto-fix Linting Issues"**
3. Click **"Run workflow"**
4. Select branch
5. Click **"Run workflow"** button

The workflow will create a PR if issues are found.

---

## 💬 3. PR Review Workflow (`lint-pr-review.yml`)

**Triggers:**
- When a PR is opened
- When commits are pushed to an open PR

**What it does:**
- Adds **inline comments** on linting issues using ReviewDog
- Shows issues in the **"Files changed"** tab
- Adds a summary comment with fix instructions

**Example PR Comment:**

```
🔍 Linting Issues Found

Your PR has some linting issues that need to be fixed before merging.

🔧 Quick Fix

Run this locally to auto-fix most issues:

./scripts/lint.sh fix

Then commit and push the changes.
```

---

## 🚦 Required Status Checks

To enforce linting on all PRs, set up **branch protection rules**:

### Setup Instructions:

1. Go to **Settings** → **Branches**
2. Add rule for `main` branch
3. Enable: **"Require status checks to pass before merging"**
4. Select: **"Run Linters"** (from `lint.yml`)
5. Save changes

Now PRs **cannot be merged** until linting passes! ✅

---

## 🔧 How to Fix Linting Issues

### Option 1: Local Fix (Recommended)

```bash
# Fix all issues automatically
./scripts/lint.sh fix

# Verify everything passes
./scripts/lint.sh check

# Commit and push
git add .
git commit -m "fix: apply linting fixes"
git push
```

### Option 2: Manual Workflow Trigger

1. Go to **Actions** tab
2. Run **"Auto-fix Linting Issues"** workflow
3. It will create a PR with fixes
4. Review and merge the auto-fix PR

### Option 3: Individual Fixes

```bash
# Fix specific issues
ruff check src/ --fix              # Fix Ruff issues
black src/ tests/                  # Format code
isort src/ tests/ --profile black  # Sort imports
```

---

## 📊 Workflow Files

| Workflow | File | Purpose |
|----------|------|---------|
| Main Linting | `.github/workflows/lint.yml` | Check linting on push/PR |
| Auto-fix | `.github/workflows/lint-autofix.yml` | Auto-create PRs with fixes |
| PR Review | `.github/workflows/lint-pr-review.yml` | Add inline comments on PRs |

---

## 🎯 Best Practices

### For Developers:

1. **Before committing:**
   ```bash
   ./scripts/lint.sh check
   ```

2. **Set up pre-commit hooks:**
   ```bash
   pre-commit install
   ```

3. **Enable auto-format in VS Code** (already configured in `.vscode/settings.json`)

### For Maintainers:

1. **Enable branch protection** to require linting checks
2. **Review auto-fix PRs** before merging
3. **Run manual auto-fix** before major releases

### For CI/CD:

1. **Linting runs first** (fastest feedback)
2. **Tests run after** linting passes
3. **Deployment happens** after all checks pass

---

## 🐛 Troubleshooting

### "Workflow failed but I don't see errors"

Check the **"Summary"** section in the Actions tab for details.

### "Auto-fix created too many changes"

Review the PR carefully. You can:
- Merge in batches (cherry-pick specific files)
- Fix manually if auto-fix is wrong
- Adjust linter config in `pyproject.toml`

### "Linting passes locally but fails in CI"

Make sure you have the same Python version:
```bash
python --version  # Should be 3.12
```

And the same dependencies:
```bash
pip install -r requirements.txt
```

### "ReviewDog not showing inline comments"

Ensure the workflow has permissions:
- Settings → Actions → General
- Workflow permissions: "Read and write permissions"

---

## 📝 Adding New Linters

To add a new linter to GitHub workflows:

1. **Update workflow files** (`.github/workflows/lint.yml`)
2. **Add to `scripts/lint.sh`**
3. **Update this documentation**
4. **Test locally first**

---

## 🔗 Related Documentation

- [Local Linting Setup](../../README.md#linting)
- [Pre-commit Hooks](../../.pre-commit-config.yaml)
- [VS Code Integration](../../.vscode/settings.json)
- [Linter Configuration](../../pyproject.toml)

---

## 📊 Monitoring

### View Workflow Runs:
https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/lint.yml

### Download Workflow Logs:
Actions → Select run → Download logs

### Check Workflow Status:
```bash
gh run list --workflow=lint.yml
```

---

**Last Updated:** 2026-02-11
**Maintained By:** Gatewayz Backend Team
