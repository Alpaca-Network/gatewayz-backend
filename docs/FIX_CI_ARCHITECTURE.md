# Cursor Auto-Fix CI Architecture

Technical architecture and implementation details for the Cursor CLI auto-fix system.

## System Architecture

### High-Level Flow

```
GitHub Event
    │
    ├─ Push to branch
    ├─ PR created
    └─ PR updated
         │
         ▼
    CI Pipeline (ci.yml)
         │
    ┌────┴────┐
    │         │
   PASS      FAIL
    │         │
    │         ▼
    │    Check Failure
    │    (check-failure job)
    │         │
    │    ┌────┴─────────────────────┐
    │    │ Is it a PR?              │
    │    │ Did CI actually fail?    │
    │    │ Classify failure type    │
    │    └────┬──────────────────────┘
    │         │
    │         ├─ No → Skip
    │         │
    │         ├─ Yes → Fix CI
    │         │    (fix-ci job)
    │         │
    │         ▼
    │    Auto-Fix Job
    │    │
    │    ├─ Checkout PR branch
    │    ├─ Download artifacts
    │    ├─ Run pytest
    │    ├─ Analyze failures
    │    ├─ Apply fixes
    │    ├─ Verify fixes
    │    ├─ Commit to branch
    │    └─ Post PR comment
    │
    ▼
Deployment Ready
```

## Workflow Triggers

### Primary Trigger: `workflow_run`

```yaml
on:
  workflow_run:
    workflows: ["CI Pipeline"]  # Listen to CI Pipeline completion
    types: [completed]           # On any completion
    branches: [main, staging, develop]
```

**Why `workflow_run`?**
- Runs in context of the main branch (not PR branch)
- Can access workflow artifacts
- Can read logs and job data
- Can push back to PR branch
- No race conditions with concurrent workflows

### Alternative: Manual Dispatch

```bash
gh workflow run fix-ci-cursor.yml \
  --ref feature/my-branch
```

## Job Structure

### Job 1: `check-failure`

**Purpose**: Determine if fixes should run

**Logic**:
```
IF CI workflow failed
  AND is pull request (has PRs)
  THEN:
    - Extract PR number
    - Classify failure type
    - Output: should_fix, pr_number, failure_reason
  ELSE:
    - Output: should_fix=false
```

**Failure Type Classification**:
- `test` - Tests failed
- `lint` - Code quality checks failed
- `build` - Build verification failed
- `security` - Security checks failed
- `unknown` - Unable to determine

**Outputs**:
```yaml
should_fix: "true" | "false"
pr_number: "123"
failure_reason: "test" | "lint" | "build" | "security" | "unknown"
```

### Job 2: `fix-ci`

**Purpose**: Analyze failures and apply fixes

**Dependencies**: `needs: check-failure`

**Condition**: `if: needs.check-failure.outputs.should_fix == 'true'`

**Steps**:

1. **Checkout PR branch**
   ```bash
   git checkout <pr-branch>
   ```
   - Gets the actual PR branch (not merge commit)
   - Allows pushing fixes back

2. **Download artifacts**
   - Test output files
   - Coverage reports
   - Logs from failed jobs

3. **Extract failure info**
   - Parse test output
   - Identify FAILED tests
   - Extract error messages

4. **Run pytest**
   ```bash
   pytest tests/ -v --tb=short
   ```
   - Captures current test state
   - Generates baseline for comparison

5. **Analyze failures**
   - Generate analysis prompt
   - Include failure context
   - Ask for targeted fixes

6. **Apply auto-fixes**
   - `isort src/` - Fix imports
   - `black src/` - Format code
   - `ruff check src/ --fix` - Auto-fixable linting

7. **Check for changes**
   - `git diff --quiet` - See if anything changed
   - Commit if changes exist

8. **Commit to branch**
   ```bash
   git commit -m "fix: auto-fix CI failures with Cursor"
   git push origin HEAD:<pr-branch>
   ```
   - Commits go directly to PR's source branch
   - No separate fix branch created
   - PR automatically updates

9. **Verify fixes**
   - Re-run pytest after fixes
   - Check if tests pass
   - Output verification result

10. **Post PR comment**
    - Success: "All fixed! ✅"
    - Partial: "Some fixed, review needed ⚠️"
    - No changes: "Already resolved ℹ️"

## Data Flow

### Artifact Handling

```
Failed CI Run
    │
    └─ Artifacts
       ├─ test-output-shard-1.txt
       ├─ test-output-shard-2.txt
       ├─ coverage-1
       ├─ coverage-2
       └─ pytest-durations
            │
            ▼
       Download in fix-ci
            │
            ├─ Extract to files
            ├─ Merge outputs
            ├─ Parse failures
            └─ Feed to analysis
```

### Change Handling

```
Source Code
    │
    ├─ Auto-fixes applied
    │  ├─ isort (imports)
    │  ├─ black (formatting)
    │  └─ ruff (linting)
    │
    ▼
Git Staging Area
    │
    ├─ git add -A
    │
    ▼
Commit (if changes exist)
    │
    ├─ Message: "fix: auto-fix CI failures with Cursor"
    │
    ▼
Push to PR branch
    │
    └─ PR auto-updates (no new comment needed)
```

## Security Considerations

### Git Configuration

```yaml
- name: Configure git
  run: |
    git config user.name "Cursor Auto-Fix Bot"
    git config user.email "cursor-autofix@cursor.sh"
```

**Why**:
- Identifies bot as commit author
- Transparent about automation
- Clear audit trail

### Permissions

```yaml
permissions:
  id-token: write        # OIDC token for GitHub
  contents: write        # Commit and push
  pull-requests: write   # Comment on PR
  checks: write          # Access check runs
  issues: write          # Create issues if needed
```

**Why each permission**:
- `id-token`: OIDC authentication
- `contents`: Git operations (push, commit)
- `pull-requests`: Post/read comments
- `checks`: Access workflow data
- `issues`: Create issues for escalation

### API Key Handling

```yaml
env:
  CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
```

**Security**:
- Stored in GitHub Secrets (encrypted)
- Only available to trusted workflows
- Used only for Cursor API calls
- Not logged or exposed in output

## Implementation Details

### Branch Context Handling

**Problem**: How to safely checkout and push to PR branch from `workflow_run`?

**Solution**: Use workflow_run context
```yaml
- uses: actions/checkout@v4
  with:
    ref: ${{ github.event.workflow_run.head_branch }}
    fetch-depth: 0  # Get full history for git operations
```

**Why**:
- `workflow_run.head_branch` = PR's source branch
- Full fetch-depth allows git operations
- Can push back to exact branch

### Failure Detection

```python
# Extract failure type from job data
for job in jobs:
    if job.conclusion == 'failure':
        if 'Test' in job.name:
            failureType = 'test'
        elif 'Lint' in job.name:
            failureType = 'lint'
        # ... etc
```

**Why this approach**:
- No need to parse logs manually
- Uses GitHub API directly
- Reliable classification
- Fast execution

### Artifact Downloads

```yaml
- uses: actions/download-artifact@v4
  with:
    # Downloads all artifacts from this repo
    # Names: test-output-shard-1.txt, etc.
```

**What it downloads**:
- From the failed CI workflow
- Automatically named artifacts
- Merged into working directory
- Ready for analysis

### Test Re-run Strategy

```bash
# First run (baseline)
pytest tests/ -v --tb=short > test_run_output.txt

# Apply fixes
# ... fix code ...

# Second run (verification)
pytest tests/ -v --tb=short > test_verify_output.txt

# Compare results
```

**Why two runs**:
1. First: Establish what's broken
2. After fixes: Verify they work
3. Easy comparison of before/after

## Error Handling

### Job Failure Handling

```yaml
run: |
  set +e  # Don't exit on failure

  # Run command
  pytest tests/ -v
  EXIT_CODE=$?

  # Capture result
  if [ $EXIT_CODE -eq 0 ]; then
    echo "success"
  else
    echo "failed"
  fi

  exit 0  # Don't fail the job
```

**Why**:
- Catch and report failures
- Don't abort workflow prematurely
- Can apply fixes even if tests fail
- Clear error reporting

### Git Operations

```bash
# Safe git operations with error handling
git add -A || echo "Nothing to add"
git commit -m "message" || echo "No changes"
git push origin HEAD:branch || echo "Push failed"
```

**Why**:
- Handle "nothing to commit" gracefully
- Report issues clearly
- Continue workflow even if push fails

### API Failures

```javascript
try {
  // GitHub API call
  const result = await github.rest.issues.createComment(...);
} catch (error) {
  console.error('Failed:', error.message);
  // Don't throw - don't fail the job
}
```

**Why**:
- API calls might fail (network, rate limits)
- Don't fail entire workflow for comment failures
- Still attempt to post if possible

## Performance Optimizations

### Parallel Processing

```yaml
# These don't depend on each other - can run in parallel:
- Download artifacts
- Setup Python
- Install dependencies
```

**Why**: Faster workflow execution

### Conditional Steps

```yaml
- name: Re-run pytest
  if: steps.changes.outputs.has_changes == 'true'
  # Only run if we actually made changes
```

**Why**: Saves time on no-change runs

### Caching

```yaml
cache:
  path: .cache/wheels
  key: ${{ runner.os }}-wheels-${{ hashFiles(...) }}
```

**Why**:
- Faster pip installs
- Reuse between runs
- Same as CI pipeline

## Monitoring and Observability

### Workflow Logs

Each major step logs status:
```
✅ Downloaded workflow artifacts
❌ Tests still failing
📝 Committing fixes...
🚀 Pushing to PR branch
```

**Why**: Easy to track progress

### Outputs

Each job outputs key data:
```yaml
outputs:
  should_fix: "true"
  pr_number: "123"
  failure_reason: "test"
  tests_passed: "true"
  changes_made: "true"
```

**Why**: Enable conditional steps, reporting

### PR Comments

Posts visible feedback on PR:
```markdown
## 🔧 Cursor Auto-Fix Results

✅ **All tests now pass!**

Cursor successfully:
- Diagnosed the failures
- Applied targeted fixes
- Verified all tests pass
```

**Why**: Developer feedback without email

## Integration Points

### With CI Pipeline

```
CI Pipeline (ci.yml)
  ├─ Triggers: push, PR
  ├─ Runs: lint, test, build
  └─ Stores: artifacts

              ↓ workflow_run on failure

Fix CI Cursor (fix-ci-cursor.yml)
  ├─ Triggers: on CI failure
  ├─ Reads: artifacts, logs
  ├─ Does: fix, test, commit
  └─ Updates: PR branch

              ↓ commits to branch

CI Pipeline (runs again)
  └─ Now should pass!
```

### With GitHub Actions

**Uses**:
- `actions/checkout@v4` - Clone repo
- `actions/setup-python@v5` - Python env
- `actions/download-artifact@v4` - Get artifacts
- `actions/github-script@v7` - GitHub API calls

**Why**: Trusted, well-tested actions

### With GitHub API

**Calls**:
- `github.rest.actions.*` - Query workflows
- `github.rest.issues.*` - Comment on PR
- OIDC token for authentication

**Why**: Programmatic access to repo data

## Configuration Patterns

### Trigger Filtering

```yaml
on:
  workflow_run:
    workflows: ["CI Pipeline"]  # Which workflow triggers this
    types: [completed]           # On completion (not start)
    branches: [main, staging]    # Only these branches
```

### Conditional Execution

```yaml
jobs:
  fix-ci:
    if: |
      needs.check-failure.outputs.should_fix == 'true' &&
      github.event_name == 'workflow_run'
```

### Job Ordering

```yaml
jobs:
  fix-ci:
    needs: check-failure  # Must run check-failure first
```

## Failure Recovery

### If Workflow Itself Fails

The workflow has `timeout-minutes: 60` to prevent hanging.

If it fails:
1. Check workflow logs for error
2. Fix the issue
3. Wait for next CI failure to retry
4. Or manually dispatch with `gh workflow run`

### If Commit Push Fails

```bash
git push origin HEAD:branch || echo "Push failed"
```

The workflow doesn't fail, just notes it in logs.

### If No Tests Pass

The workflow notes this in the PR comment:
```
⚠️ **Some tests still failing after auto-fix**
```

Developer can then manually investigate.

## Debugging

### Enable Debug Logging

```yaml
- name: Debug info
  run: |
    echo "Branch: ${{ github.event.workflow_run.head_branch }}"
    echo "PR: ${{ needs.check-failure.outputs.pr_number }}"
    echo "Failure: ${{ needs.check-failure.outputs.failure_reason }}"
    git log --oneline -5
    git status
```

### Check Artifact Contents

```bash
unzip -l artifacts.zip
find . -name "*.txt" -type f -exec head -20 {} \;
```

### Verify Git State

```bash
git remote -v
git branch -a
git config user.name
```

## Related Components

- **CI Pipeline**: `.github/workflows/ci.yml`
- **Secrets**: GitHub Settings → Secrets
- **API Key**: Cursor dashboard
- **Test Suite**: `tests/` directory
- **Code Quality**: `pyproject.toml`

---

**Last Updated**: 2025-11-24
**Version**: 1.0
**Complexity**: Advanced - Contains GitHub Actions, Cursor CLI, Git operations
