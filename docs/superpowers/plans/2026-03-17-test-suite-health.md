# Test Suite Health Fix — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 828 test failures and 948 skips that silently pass CI, delivered as 5 independent PRs in priority order.

**Architecture:** Each PR is self-contained and mergeable independently. PR1 (rate limiting) should land before PR2 (CI truthfulness) so CI doesn't go red on merge. PR3-PR5 can land in any order after PR2.

**Tech Stack:** GitHub Actions, pytest, FastAPI TestClient, monkeypatch fixtures

---

## PR 1: Disable Rate Limiting in Tests (fixes 524 tests)

### Task 1: Add rate limiting bypass to root conftest

**Files:**
- Modify: `tests/conftest.py`
- Modify: `src/middleware/security_middleware.py:576` (dispatch method)

The `SecurityMiddleware.dispatch()` rate-limits TestClient requests because there's no bypass for test environments. The e2e conftest already mocks rate limiting (line 176-189), but the root conftest doesn't.

**Approach:** Add `TESTING` env var check in `SecurityMiddleware.dispatch()` to skip rate limiting when `TESTING=true` (already set in conftest line 14). This is simpler and more robust than mocking — it ensures the middleware itself knows to stand down.

- [ ] **Step 1: Add test bypass to SecurityMiddleware.dispatch()**

In `src/middleware/security_middleware.py`, at the top of `dispatch()` (after the health endpoint skip on line 578), add:

```python
# Skip rate limiting in test environment
if os.environ.get("TESTING") == "true":
    return await call_next(request)
```

Also add `import os` at the top of the file if not already present.

- [ ] **Step 2: Verify the TESTING env var is already set in conftest**

Confirm `tests/conftest.py` line 14 already has:
```python
os.environ.setdefault("TESTING", "true")
```

No change needed — this is already there.

- [ ] **Step 3: Run a sample of previously-failing tests locally**

Run: `pytest tests/routes/ -v --tb=short -x --timeout=30 -n0 2>&1 | head -80`
Expected: Tests that previously got 429 should now pass or fail with a non-429 error.

- [ ] **Step 4: Commit**

```bash
git add src/middleware/security_middleware.py
git commit -m "fix: bypass security middleware rate limiting in test environment

Checks TESTING=true env var (already set by conftest) to skip IP and
fingerprint rate limiting during tests. Fixes 524 tests that were
getting 429 instead of expected responses.

Closes part of #2076"
```

---

## PR 2: Fix CI Pipeline Truthfulness

### Task 2: Make CI fail when tests fail

**Files:**
- Modify: `.github/workflows/ci.yml:190` (test step)
- Modify: `.github/workflows/ci.yml:296-311` (coverage job check)

The `set +e` on line 190 prevents bash from exiting on pytest failure. While `exit $TEST_EXIT_CODE` on line 214 should propagate the failure, the `| tee` pipe on line 201 masks the exit code — `$?` captures tee's exit code (0), not pytest's.

- [ ] **Step 1: Fix the pipe exit code issue**

Replace the test run block in `.github/workflows/ci.yml` (lines 190-214):

```yaml
      run: |
        set -o pipefail  # Ensure pipe returns pytest's exit code, not tee's
        pytest tests/ -v --tb=short \
          -n auto \
          --dist=loadfile \
          --splits 4 --group ${{ matrix.shard }} \
          --cov=src \
          --cov-report=xml \
          --cov-report=term \
          -m "not smoke and not benchmark" 2>&1 | tee test-output-shard-${{ matrix.shard }}.txt
```

Key changes:
- Replace `set +e` with `set -o pipefail` so the pipe returns pytest's exit code
- Remove the manual `TEST_EXIT_CODE` capture and `exit` — bash will exit with pytest's code naturally

- [ ] **Step 2: Verify the coverage job gates properly**

The `build` job on line 413 has `needs: [lint, coverage]`. The `coverage` job has `if: always()` on line 255, which means it runs even if tests fail. The `build` job will still fail because `test` job failure propagates through `needs`.

Confirm this by checking: the `build` job (line 413) depends on `coverage`, which depends on `test` (line 254). If `test` fails, `coverage` runs (due to `if: always()`) but `build` sees `test` as failed in its dependency chain.

Actually — `build` depends on `[lint, coverage]`, NOT on `test`. Since `coverage` has `if: always()`, it will succeed even when `test` fails. This means `build` runs even when tests fail.

Fix: Add `test` to the build job's needs:

```yaml
  build:
    name: Build Verification
    runs-on: blacksmith-4vcpu-ubuntu-2404
    needs: [lint, test, coverage]
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "fix: make CI fail when tests fail

Replace set +e with set -o pipefail so pytest exit code propagates
through the tee pipe. Add 'test' to build job needs so test failures
block the build step.

Closes part of #2076"
```

---

## PR 3: Fix Stale Mocks and Fixtures (fixes ~98 tests)

### Task 3A: Add missing `client` fixture to root conftest

**Files:**
- Modify: `tests/conftest.py`

32 tests fail because they use a `client` fixture that only exists in `tests/e2e/conftest.py`. We need a lightweight version in the root conftest.

- [ ] **Step 1: Add `client` fixture to root conftest**

Add to `tests/conftest.py` (after the existing imports and before the factory fixtures):

```python
from unittest.mock import AsyncMock, Mock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient with mocked dependencies for unit/route tests."""
    # Set TESTING env so SecurityMiddleware skips rate limiting
    os.environ["TESTING"] = "true"

    from src.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
```

- [ ] **Step 2: Verify fixture is picked up**

Run: `pytest tests/routes/test_ping.py -v --timeout=30 -n0 2>&1 | head -20`
Expected: Tests that use `client` fixture should no longer fail with "fixture not found".

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "fix: add client fixture to root conftest

Adds a TestClient fixture available to all test directories, not just
e2e. 32 tests were failing because they depended on a client fixture
that only existed in tests/e2e/conftest.py.

Closes part of #2076"
```

### Task 3B: Fix `calculate_cost` import references

**Files:**
- Search and fix test files referencing `src.routes.chat.calculate_cost`

20 tests mock `src.routes.chat.calculate_cost` but it was moved to `src.services.credit_handler` or `src.services.credit_precheck`.

- [ ] **Step 1: Find all stale references**

Run: `grep -rn "routes.chat.*calculate_cost\|routes\.chat\.calculate" tests/`

- [ ] **Step 2: Update each reference**

Replace `src.routes.chat.calculate_cost` with the correct current module path (found in exploration: `src.services.credit_handler` or `src.services.credit_precheck`).

- [ ] **Step 3: Run affected tests**

Run: `pytest tests/ -k "calculate_cost" -v --timeout=30 -n0 2>&1 | head -40`

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "fix: update calculate_cost mock paths after refactor

calculate_cost was moved from src.routes.chat to src.services but
test mocks still patched the old location.

Closes part of #2076"
```

### Task 3C: Fix `log_security_event` mock issue

**Files:**
- Modify: `tests/conftest.py`

364 ERROR logs from `log_security_event` because tests don't mock the security audit table. Since PR1 bypasses the middleware entirely in tests, this is already fixed — `log_security_event` is only called from rate limiting code paths in the middleware. Verify after PR1 lands.

- [ ] **Step 1: Verify log_security_event errors are gone after PR1**

Run: `pytest tests/ -v --timeout=30 -n0 2>&1 | grep -c "log_security_event"`
Expected: 0 occurrences (middleware is bypassed in tests).

- [ ] **Step 2: If still occurring, add mock to conftest**

```python
@pytest.fixture(autouse=True)
def mock_security_audit(monkeypatch):
    """Prevent security audit logging from hitting real DB in tests."""
    monkeypatch.setattr(
        "src.db.activity.log_security_event",
        lambda **kwargs: None,
    )
```

- [ ] **Step 3: Commit if needed**

```bash
git add tests/conftest.py
git commit -m "fix: mock log_security_event to prevent DB errors in tests"
```

---

## PR 4: Separate Integration Tests from Unit Tests (fixes 948 skips)

### Task 4A: Mark integration tests with proper markers

**Files:**
- Modify: Multiple test files in `tests/db/`, `tests/integration/`
- Modify: `pytest.ini`

~880 tests skip because they need a real database. These should be explicitly marked as `@pytest.mark.integration` and excluded from the default CI run.

- [ ] **Step 1: Add integration marker to DB tests**

Find all test files in `tests/db/` and add `@pytest.mark.integration` to test classes or functions that use `supabase_client` fixture or make real DB calls.

Run: `grep -rln "supabase_client\|get_supabase_client" tests/db/`

Add `import pytest` and `@pytest.mark.integration` to each.

- [ ] **Step 2: Update CI to exclude integration tests**

In `.github/workflows/ci.yml`, change the pytest marker filter:

```yaml
-m "not smoke and not benchmark and not integration"
```

- [ ] **Step 3: Update pytest.ini default marker**

Change line 34 from:
```
-m "not smoke"
```
to:
```
-m "not smoke and not integration"
```

- [ ] **Step 4: Simplify skip_if_no_database fixture**

Now that integration tests are explicitly marked and excluded from CI, the `skip_if_no_database` autouse fixture in `tests/conftest.py` can be simplified or removed. Keep it as a safety net but simplify:

```python
@pytest.fixture(autouse=True)
def skip_if_no_database(request):
    """Skip integration tests if database is unavailable."""
    markers = [m.name for m in request.node.iter_markers()]
    if "integration" not in markers:
        return  # Not an integration test, don't check DB

    if not hasattr(skip_if_no_database, "_db_available"):
        try:
            client = get_supabase_client()
            client.table("users").select("id").limit(1).execute()
            skip_if_no_database._db_available = True
        except Exception as e:
            skip_if_no_database._db_available = False
            skip_if_no_database._db_error = str(e)

    if not skip_if_no_database._db_available:
        pytest.skip(f"Database not available: {skip_if_no_database._db_error}")
```

- [ ] **Step 5: Commit**

```bash
git add tests/ pytest.ini .github/workflows/ci.yml
git commit -m "fix: separate integration tests from unit tests

Mark DB-dependent tests with @pytest.mark.integration and exclude them
from default CI runs. This converts 880 silent skips into properly
categorized tests that only run when a database is available.

Closes part of #2076"
```

---

## PR 5: Fix or Delete Broken Test Files (fixes 6 collection errors)

### Task 5: Fix broken imports or remove dead test files

**Files:**
- `tests/config/test_railway_config.py` — missing module
- `tests/db/test_models_catalog_db.py` — missing module
- `tests/health/test_gateway_health.py` — FileNotFoundError
- `tests/services/test_aimo_resilience.py` — import error
- `tests/services/test_anthropic_models.py` — import error
- `tests/utils/test_error_handlers.py` — import error

- [ ] **Step 1: Check each file for fixability**

For each file, try to import it and see what's missing:

```bash
python -c "import tests.config.test_railway_config" 2>&1
python -c "import tests.db.test_models_catalog_db" 2>&1
python -c "import tests.health.test_gateway_health" 2>&1
python -c "import tests.services.test_aimo_resilience" 2>&1
python -c "import tests.services.test_anthropic_models" 2>&1
python -c "import tests.utils.test_error_handlers" 2>&1
```

- [ ] **Step 2: For each file, decide fix or delete**

- If the module it tests still exists → fix the import
- If the module was removed/renamed → delete the test file

- [ ] **Step 3: Fix fixable files, delete dead ones**

Update imports to match current module paths, or `git rm` files that test removed modules.

- [ ] **Step 4: Verify no more collection errors**

Run: `pytest --collect-only 2>&1 | grep "ERROR"`
Expected: No collection errors.

- [ ] **Step 5: Commit**

```bash
git add -A tests/
git commit -m "fix: resolve test collection errors from broken imports

Fix or remove 6 test files that fail to import due to renamed or
removed source modules.

Closes #2076"
```

---

## PR Merge Order

1. **PR 1** (rate limiting bypass) — Fixes 524 failures, safe to merge immediately
2. **PR 2** (CI truthfulness) — Now CI goes red honestly, but most failures are already fixed
3. **PR 3** (stale mocks) — Fixes another ~98 failures
4. **PR 4** (integration separation) — Eliminates 880+ skips from CI output
5. **PR 5** (broken imports) — Fixes 6 collection errors

After all 5 PRs: CI should report ~200-300 remaining failures (stale assertions from Category 4 + miscellaneous), down from 828. Those are individual test fixes that can be addressed incrementally.
