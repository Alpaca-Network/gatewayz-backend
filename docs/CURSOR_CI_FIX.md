# Cursor CLI Auto-Fix Integration

## Overview

This project uses **Cursor CLI** to automatically detect, diagnose, and fix CI failures. When the CI pipeline fails on a pull request, the auto-fix workflow activates and commits fixes directly to the PR's branch.

## How It Works

### Workflow Flow

```
┌─────────────────────────────┐
│   PR pushed to remote       │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│   CI Pipeline runs          │
│   (ci.yml)                  │
└──────────────┬──────────────┘
               │
        ┌──────┴──────┐
        │             │
        ▼             ▼
    PASS          FAIL
        │             │
        │             ▼
        │    ┌─────────────────────────┐
        │    │  Fix CI workflow        │
        │    │  (fix-ci-cursor.yml)    │
        │    │                         │
        │    │  1. Detect failure      │
        │    │  2. Analyze logs        │
        │    │  3. Apply fixes         │
        │    │  4. Verify tests        │
        │    │  5. Commit to branch    │
        │    └────────┬────────────────┘
        │             │
        │             ▼
        │    ┌─────────────────────────┐
        │    │  PR updated with fixes  │
        │    │  (same branch)          │
        │    └─────────────────────────┘
        │
        ▼
   Deploy when ready
```

### Key Features

- **Automatic Detection**: Monitors CI pipeline failures
- **Smart Diagnosis**: Analyzes logs to identify root causes
- **Targeted Fixes**: Applies minimal, focused changes
- **Same Branch Commits**: Fixes go directly to PR branch (no separate fix branch)
- **Verification**: Runs tests after fixes to confirm they work
- **PR Comments**: Posts status updates on the PR

## Setup

### 1. Add Cursor API Key Secret

Add your Cursor API key as a GitHub secret:

```bash
# Go to: Settings → Secrets and variables → Actions
# Add new secret: CURSOR_API_KEY
# Value: your-cursor-api-key-here
```

### 2. Workflow Files

The integration includes two workflow files:

#### `fix-ci-cursor.yml` (Main auto-fix workflow)
- Triggers on CI pipeline failures
- Runs analysis and fixes
- Commits directly to PR branch
- Posts PR comments with results

#### `ci.yml` (Updated)
- Modified to log when auto-fix is triggered
- Still runs all quality checks
- Simplified trigger section

### 3. Configuration

The workflow is configured to:
- Fix code quality issues (linting, formatting)
- Fix failing tests
- Fix build issues
- Handle multiple failure types

## Triggering the Auto-Fix

### Automatic (Recommended)

The fix-ci workflow automatically triggers when:

1. ✅ CI pipeline fails (any job)
2. ✅ Failure is on a pull request (not push to main)
3. ✅ Can be diagnosed from logs

### Manual Dispatch

You can also manually trigger the workflow:

```bash
gh workflow run fix-ci-cursor.yml
```

## Workflow Behavior

### When It Runs

```
Event: workflow_run (CI Pipeline completion)
  ├─ Trigger: "CI Pipeline" workflow fails
  ├─ Branches: main, staging, develop
  └─ Condition: Is a pull request
```

### What It Does

1. **Check Failure** (Job: `check-failure`)
   - Detects if CI actually failed
   - Identifies PR number
   - Categorizes failure type (test, lint, build, security)

2. **Analyze & Fix** (Job: `fix-ci`)
   - Checks out PR branch
   - Downloads artifacts (test output, logs)
   - Runs pytest to identify failures
   - Applies auto-fixes (imports, formatting, etc.)
   - Re-runs tests to verify
   - Commits changes directly to PR branch

3. **Report Results** (PR Comment)
   - Success: "All CI issues fixed! ✅"
   - Partial: "Some issues fixed, additional work needed"
   - No changes: "No changes needed or already resolved"

## Commit Messages

Auto-fix commits follow this format:

```
fix: auto-fix CI failures with Cursor

This commit contains automated fixes for:
- Code quality issues (ruff, black, isort)
- Test failures

🤖 Generated with Cursor CLI

Co-Authored-By: Cursor Auto-Fix <cursor@cursor.sh>
```

## Failure Type Handling

### Test Failures

The workflow will:
1. Run full test suite
2. Extract failure details
3. Identify root causes
4. Apply targeted fixes
5. Re-run tests to verify

### Lint/Code Quality Failures

The workflow will:
1. Run ruff, black, isort checks
2. Auto-fix what it can
3. Commit formatting changes
4. Run tests to ensure no regressions

### Build Failures

The workflow will:
1. Test app startup
2. Check Railway configs
3. Verify dependencies
4. Fix critical issues

## Permissions Required

The workflow needs these GitHub permissions:

```yaml
permissions:
  id-token: write        # For OIDC token
  contents: write        # To commit and push
  pull-requests: write   # To comment on PRs
  checks: write          # To check status
  issues: write          # For issue creation
```

These are configured in `fix-ci-cursor.yml`.

## Secrets Required

### CURSOR_API_KEY (Required)

Your Cursor CLI API key from https://cursor.com

1. Get your key from Cursor dashboard
2. Add to GitHub Secrets: `CURSOR_API_KEY`
3. Workflow will use it automatically

## Understanding the Output

### Workflow Run Logs

Check the workflow logs to see:

```
✅ Detected PR #123 with test failures
🔍 Analyzing failures...
🧪 Running test suite...
🛠️ Attempting automatic fixes...
📝 Committing fixes...
🚀 Pushing to PR branch...
✅ Fixes pushed to: feature/my-feature
```

### PR Comments

The workflow posts results on the PR:

**Success Case:**
```
## 🔧 Cursor Auto-Fix Results

✅ **All CI issues fixed!**

Cursor successfully:
- Diagnosed the failures
- Applied targeted fixes
- Verified all tests pass

Fixes have been committed and pushed to this PR.
```

**Partial Fix Case:**
```
## 🔧 Cursor Auto-Fix Results

⚠️ **Fixes applied but some issues remain**

Cursor applied fixes for code quality and identified issues, but additional debugging may be needed.

**Failure Type**: test

Please check the workflow run for detailed logs.
```

## Best Practices

### For Developers

1. **Trust the Auto-Fix**: Let it attempt fixes before manual debugging
2. **Review Changes**: Check the commits that are pushed
3. **Run Tests Locally**: Verify fixes work in your environment
4. **Provide Context**: PR descriptions help the auto-fix understand intent

### For Maintainers

1. **Monitor Success Rate**: Track how often auto-fix resolves issues
2. **Review Patterns**: See what types of failures are common
3. **Update Prompts**: Refine the failure analysis prompts as needed
4. **Escalate When Needed**: Manual intervention for complex issues

## Troubleshooting

### Workflow doesn't trigger

**Problem**: Cursor auto-fix workflow isn't running after CI failure

**Solution**:
1. Verify workflow file exists: `.github/workflows/fix-ci-cursor.yml`
2. Check CI workflow failure: Make sure it actually failed
3. Check PR context: Must be a pull request (not push to main)
4. Review workflow logs: Check CI pipeline's "Notify Cursor Auto-Fix" job

### Fixes not applied

**Problem**: Workflow runs but no commits are made

**Solution**:
1. Check if CI actually passed (no fixes needed)
2. Review "Analyze & Fix" job logs
3. Look for error messages in pytest output
4. Verify test environment has all dependencies
5. Check git status in workflow logs

### Cursor API Key issues

**Problem**: "API key invalid" or authentication errors

**Solution**:
1. Verify CURSOR_API_KEY is set in GitHub Secrets
2. Check key hasn't expired
3. Ensure key has correct permissions
4. Test key locally: `cursor auth status`

### Fix verification fails

**Problem**: Fixes are applied but tests still fail

**Solution**:
1. The workflow will note this in PR comments
2. Manual debugging may be needed
3. Check if issue is flaky test vs real failure
4. Review the "Re-run pytest" step logs

## Advanced Configuration

### Customizing Failure Analysis

Edit the prompt in `.github/workflows/fix-ci-cursor.yml`:

```yaml
- name: Run Cursor CLI to analyze and fix
  with:
    script: |
      # Customize the analysisPrompt variable for your needs
```

### Changing Trigger Conditions

Modify the `check-failure` job to adjust when fixes should run:

```yaml
if: conclusion === 'failure' && isPR
   # Customize this logic
```

### Adding Post-Fix Actions

Add steps after "Push fixes to PR branch" to:
- Notify Slack
- Create follow-up issues
- Trigger additional workflows
- Update project boards

## Integration with Existing Workflows

### With Deploy Workflow

The deploy workflow can still run normally:
1. Auto-fix commits changes
2. CI re-runs and passes
3. Deploy workflow triggers if on main

### With Lint Checks

The auto-fix works alongside linting:
1. Lint checks still run in CI
2. Auto-fix applies formatting
3. Tests verify no regressions

### With Manual Fixes

If a developer manually fixes the issue before auto-fix runs:
1. The workflow will still trigger
2. It will detect no additional changes needed
3. It will post "already resolved" message

## Examples

### Example: Test Failure Fix

```
PR #42: Add new user endpoint

❌ CI Failed - test_new_user_endpoint

↓ Auto-fix detects test failure
↓ Analyzes code
↓ Finds missing import in src/routes/users.py
↓ Applies fix
↓ Re-runs tests
✅ Tests pass

→ Commit pushed: "fix: auto-fix CI failures with Cursor"
→ PR updated automatically
```

### Example: Lint Failure Fix

```
PR #43: Refactor service layer

❌ CI Failed - Code Quality Checks

↓ Auto-fix detects lint failures
↓ Runs black, isort, ruff
↓ Fixes formatting and imports
✅ All checks pass

→ Commit pushed: "fix: auto-fix CI failures with Cursor"
→ PR updated automatically
```

## FAQ

### Q: Does auto-fix create a separate branch?

**A:** No! Fixes are committed directly to the PR's source branch, keeping everything together.

### Q: Can I disable auto-fix for a PR?

**A:** Add a label or commit message indicator, then update the workflow condition:
```yaml
if: needs.check-failure.outputs.should_fix == 'true' && !contains(github.event.pull_request.labels.*.name, 'no-autofix')
```

### Q: What if auto-fix breaks something?

**A:**
1. Workflow runs tests after fixes
2. Tests failing = fix not committed
3. You'll see warning in PR comments
4. You can revert commits and manually fix

### Q: How long does auto-fix take?

**A:** Typically 2-5 minutes depending on:
- Number of tests
- Complexity of failures
- AI analysis time

### Q: Can I use this without Cursor API key?

**A:** The workflow will still run but won't have AI analysis. You can manually implement fixes or add a different AI backend.

## Related Documentation

- [CI Pipeline](ci.md)
- [Test Coverage Guide](../README.md#testing)
- [GitHub Actions Workflows](.github/workflows/)
- [Cursor CLI Documentation](https://cursor.com/docs/cli)

## Support

For issues with the auto-fix workflow:

1. Check workflow logs in Actions tab
2. Review this documentation
3. Check Cursor CLI status: `cursor --version`
4. Open an issue with workflow logs attached

---

**Last Updated**: 2025-11-24
**Version**: 1.0
**Status**: Production Ready
