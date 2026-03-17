# O1: Billing Integrity Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every credit transaction is atomic, auditable, and correct — no silent over-charges, under-charges, or ghost deductions.

**Architecture:** Five key results executed in order of effort/impact ratio. KR2 (pricing pre-check) moves the existing guard to fire before the provider call instead of after. KR4 (trial config) is config cleanup. KR1 (atomicity) requires a Supabase migration + RPC. KR3 (refund clarity) is mostly documentation + small code changes since the current behavior is largely correct. KR5 (tests) runs throughout.

**Tech Stack:** Python 3.10+, FastAPI, Supabase (PostgreSQL), pytest, pytest-asyncio

**Refs:** #2058, Delta Report P0-2/P0-3/P0-4/P0-7, Stability Requirements S2/S6

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/services/pricing.py` | Modify | Consolidate HIGH_VALUE_MODEL_PATTERNS to single import from config |
| `src/routes/chat.py` | Modify | Add `get_model_pricing_async()` call before provider dispatch (~line 1918) |
| `src/config/usage_limits.py` | Modify | Add HIGH_VALUE_MODEL_PATTERNS as canonical source |
| `src/db/api_keys.py` | Modify | Import trial constants from `usage_limits.py` instead of hardcoding |
| `src/db/trials.py` | Modify | Change default `trial_days` from 14 to `TRIAL_DURATION_DAYS` |
| `supabase/migrations/20260316000001_atomic_credit_deduction.sql` | Create | RPC function for atomic deduct + log |
| `src/db/users.py` | Modify | Use new RPC in `deduct_credits()`, add rollback on log failure as fallback |
| `tests/services/test_pricing_precheck.py` | Create | Tests for pricing pre-check guard |
| `tests/db/test_credit_atomicity.py` | Create | Tests for atomic deduction RPC |
| `tests/db/test_trial_config_consistency.py` | Create | Tests for trial config alignment |
| `tests/services/test_refund_paths.py` | Create | Tests verifying no-deduction-on-error behavior |
| `tests/services/test_billing_integration.py` | Create | Tests for billing correctness gaps |

---

## Chunk 1: KR2 — Pricing Pre-Check Before Provider Call

### Design Note

`get_model_pricing()` already raises `ValueError` for high-value models when pricing is missing (lines 484-488 of `pricing.py`). The problem is NOT that the guard is missing — it's that `get_model_pricing()` is only called AFTER the provider returns tokens. The fix is to call `get_model_pricing_async()` BEFORE the provider dispatch in `chat.py`, so the existing guard fires pre-inference. No new function needed.

### Task 1: Consolidate HIGH_VALUE_MODEL_PATTERNS to config

**Files:**
- Modify: `src/config/usage_limits.py`
- Modify: `src/services/pricing.py:449,629,692`

- [ ] **Step 1: Write failing test — patterns are importable from config**

```python
# tests/services/test_pricing_precheck.py
from src.config.usage_limits import HIGH_VALUE_MODEL_PATTERNS

def test_high_value_patterns_exist():
    assert isinstance(HIGH_VALUE_MODEL_PATTERNS, list)
    assert len(HIGH_VALUE_MODEL_PATTERNS) > 0
    assert "gpt-4" in HIGH_VALUE_MODEL_PATTERNS
    assert "claude-3" in HIGH_VALUE_MODEL_PATTERNS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_pricing_precheck.py::test_high_value_patterns_exist -v`
Expected: FAIL with `ImportError: cannot import name 'HIGH_VALUE_MODEL_PATTERNS'`

- [ ] **Step 3: Add patterns to usage_limits.py**

Add to end of `src/config/usage_limits.py`:

```python
# High-Value Model Patterns
# Models matching these patterns MUST have real pricing data.
# Requests are blocked if only default pricing ($0.00002/token) is available.
# NOTE: This is the canonical list. Do NOT define local copies elsewhere.
HIGH_VALUE_MODEL_PATTERNS = [
    "gpt-4", "gpt-5", "o1-", "o3-", "o4-",
    "claude-3", "claude-opus", "claude-sonnet-4",
    "gemini-1.5-pro", "gemini-2", "gemini-pro",
    "command-r-plus", "mixtral-8x22b",
]
```

Note: This matches the existing list exactly. Do NOT add new patterns (e.g., `claude-4`) in this task — that's a separate functional change.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_pricing_precheck.py::test_high_value_patterns_exist -v`
Expected: PASS

- [ ] **Step 5: Update pricing.py to import from config (3 locations)**

In `src/services/pricing.py`, add import at top:

```python
from src.config.usage_limits import HIGH_VALUE_MODEL_PATTERNS
```

Then delete the local `HIGH_VALUE_MODEL_PATTERNS = [...]` list definitions at lines ~449, ~629, and ~692. Each defines an identical local variable — replace with the import.

- [ ] **Step 6: Run existing pricing tests to verify no regression**

Run: `pytest tests/services/test_pricing.py tests/services/test_pricing_accuracy.py tests/services/test_pricing_validation.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/config/usage_limits.py src/services/pricing.py tests/services/test_pricing_precheck.py
git commit -m "refactor: consolidate HIGH_VALUE_MODEL_PATTERNS to single config location"
```

---

### Task 2: Wire pricing pre-check into chat route before provider call

**Files:**
- Modify: `src/routes/chat.py:~1918`

- [ ] **Step 1: Write test — chat.py imports get_model_pricing_async for pre-check**

```python
# tests/services/test_pricing_precheck.py (append)
def test_chat_imports_pricing_function_for_precheck():
    """chat.py must import get_model_pricing_async for pre-inference pricing validation."""
    from pathlib import Path
    source = Path("src/routes/chat.py").read_text()
    assert "get_model_pricing_async" in source, (
        "chat.py must import get_model_pricing_async for pre-inference pricing validation"
    )

def test_chat_pricing_precheck_before_provider_dispatch():
    """Pricing pre-check must appear BEFORE provider dispatch in chat.py."""
    from pathlib import Path
    source = Path("src/routes/chat.py").read_text()
    # The pre-check call should appear before the PROVIDER_ROUTING dispatch
    precheck_pos = source.find("await get_model_pricing_async(req.model)")
    dispatch_pos = source.find("PROVIDER_ROUTING[attempt_provider]")
    assert precheck_pos > 0, "get_model_pricing_async pre-check not found in chat.py"
    assert precheck_pos < dispatch_pos, (
        f"Pricing pre-check (pos {precheck_pos}) must appear before "
        f"provider dispatch (pos {dispatch_pos})"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/test_pricing_precheck.py -v -k "chat"`
Expected: FAIL (get_model_pricing_async not yet called pre-dispatch)

- [ ] **Step 3: Add the pre-check to chat.py**

In `src/routes/chat.py`, add import near line 444:

```python
from src.services.pricing import get_model_pricing_async
```

Then after the credit check at line ~1918 (`raise APIExceptions.payment_required`), add:

```python
        # === 1.5) Pricing pre-check: block high-value models without pricing ===
        # BEFORE hitting any upstream provider. get_model_pricing_async() raises
        # ValueError for high-value models (GPT-4, Claude 3+, etc.) if only
        # default pricing ($0.00002/token) is available. This prevents paying
        # upstream providers for tokens we can't bill to the user.
        if not is_anonymous:
            try:
                await get_model_pricing_async(req.model)
            except ValueError as pricing_err:
                logger.warning(
                    "Pricing pre-check failed (request_id=%s, model=%s): %s",
                    request_id, req.model, str(pricing_err),
                )
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": {
                            "message": str(pricing_err),
                            "type": "pricing_unavailable",
                            "code": "model_pricing_missing",
                        }
                    },
                )
```

- [ ] **Step 4: Run all pricing precheck tests**

Run: `pytest tests/services/test_pricing_precheck.py -v`
Expected: All PASS

- [ ] **Step 5: Verify existing chat tests still pass**

Run: `pytest tests/routes/test_chat.py -v --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/routes/chat.py tests/services/test_pricing_precheck.py
git commit -m "fix: add pricing pre-check before provider call to prevent unbillable usage

Closes KR2 of #2058"
```

---

## Chunk 2: KR4 — Trial Configuration Reconciliation

### Task 3: Fix trial config to use single source of truth

**Files:**
- Modify: `src/db/trials.py:13` (import at top of file)
- Modify: `src/db/api_keys.py:193,205` (import at top of file)

- [ ] **Step 1: Write failing test — trial uses config values**

```python
# tests/db/test_trial_config_consistency.py
import inspect
from src.config.usage_limits import TRIAL_DURATION_DAYS, TRIAL_CREDITS_AMOUNT


def test_trial_duration_is_3_days():
    assert TRIAL_DURATION_DAYS == 3


def test_trial_credits_is_5_dollars():
    assert TRIAL_CREDITS_AMOUNT == 5.0


def test_start_trial_default_matches_config():
    """start_trial_for_key() default trial_days must match config."""
    from src.db.trials import start_trial_for_key
    sig = inspect.signature(start_trial_for_key)
    default_days = sig.parameters["trial_days"].default
    assert default_days == TRIAL_DURATION_DAYS, (
        f"start_trial_for_key default trial_days={default_days} "
        f"but TRIAL_DURATION_DAYS={TRIAL_DURATION_DAYS}"
    )


def test_api_key_creation_uses_config_constants():
    """api_keys.py should import from usage_limits, not hardcode values."""
    from pathlib import Path
    source = Path("src/db/api_keys.py").read_text()
    assert "TRIAL_CREDITS_AMOUNT" in source, (
        "api_keys.py should import TRIAL_CREDITS_AMOUNT from usage_limits"
    )
    assert "TRIAL_DURATION_DAYS" in source, (
        "api_keys.py should import TRIAL_DURATION_DAYS from usage_limits"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_trial_config_consistency.py -v`
Expected: FAIL on `test_start_trial_default_matches_config` (default is 14, config is 3)

- [ ] **Step 3: Fix trials.py — add import at top of file, change default**

At the TOP of `src/db/trials.py` (with other imports), add:

```python
from src.config.usage_limits import TRIAL_DURATION_DAYS
```

Then change the function signature at line 13:

```python
# Before:
def start_trial_for_key(api_key: str, trial_days: int = 14) -> dict[str, Any]:
# After:
def start_trial_for_key(api_key: str, trial_days: int = TRIAL_DURATION_DAYS) -> dict[str, Any]:
```

- [ ] **Step 4: Fix api_keys.py — add import at top, replace hardcoded values**

At the TOP of `src/db/api_keys.py` (with other imports), add:

```python
from src.config.usage_limits import TRIAL_DURATION_DAYS, TRIAL_CREDITS_AMOUNT
```

Replace hardcoded values at lines ~193 and ~205:

```python
# Before: trial_end = trial_start + timedelta(days=3)
# After:
trial_end = trial_start + timedelta(days=TRIAL_DURATION_DAYS)

# Before: "trial_credits": 5.0 if is_trial else 0.0,
# After:
"trial_credits": TRIAL_CREDITS_AMOUNT if is_trial else 0.0,
```

- [ ] **Step 5: Run all trial-related tests**

Run: `pytest tests/db/test_trials.py tests/db/test_trial_config_consistency.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/db/trials.py src/db/api_keys.py tests/db/test_trial_config_consistency.py
git commit -m "fix: reconcile trial config to single source of truth in usage_limits.py

Closes KR4 of #2058"
```

---

## Chunk 3: KR1 — Atomic Credit Deduction

### Task 4: Create Supabase RPC for atomic deduct + log

**Files:**
- Create: `supabase/migrations/20260316000001_atomic_credit_deduction.sql`

- [ ] **Step 1: Write the migration**

Create `supabase/migrations/20260316000001_atomic_credit_deduction.sql`:

```sql
-- Atomic credit deduction: balance update + transaction log in one transaction.
-- Prevents the scenario where credits are deducted but the transaction log fails.

CREATE OR REPLACE FUNCTION deduct_credits_atomic(
    p_user_id BIGINT,
    p_amount NUMERIC,
    p_description TEXT,
    p_transaction_type VARCHAR(50),
    p_allowance_before NUMERIC,
    p_purchased_before NUMERIC,
    p_from_allowance NUMERIC,
    p_from_purchased NUMERIC,
    p_metadata JSONB DEFAULT '{}'::JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_allowance_after NUMERIC;
    v_purchased_after NUMERIC;
    v_balance_before NUMERIC;
    v_balance_after NUMERIC;
    v_transaction_id BIGINT;
    v_updated_rows INT;
BEGIN
    v_allowance_after := p_allowance_before - p_from_allowance;
    v_purchased_after := p_purchased_before - p_from_purchased;
    v_balance_before := p_allowance_before + p_purchased_before;
    v_balance_after := v_allowance_after + v_purchased_after;

    UPDATE users
    SET subscription_allowance = v_allowance_after,
        purchased_credits = v_purchased_after,
        updated_at = NOW()
    WHERE id = p_user_id
      AND subscription_allowance = p_allowance_before
      AND purchased_credits = p_purchased_before;

    GET DIAGNOSTICS v_updated_rows = ROW_COUNT;

    IF v_updated_rows = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'reason', 'concurrent_modification',
            'message', 'Balance changed between read and update'
        );
    END IF;

    INSERT INTO credit_transactions (
        user_id, amount, transaction_type, description,
        balance_before, balance_after, metadata, created_at
    ) VALUES (
        p_user_id, -p_amount, p_transaction_type, p_description,
        v_balance_before, v_balance_after, p_metadata, NOW()
    )
    RETURNING id INTO v_transaction_id;

    RETURN jsonb_build_object(
        'success', true,
        'transaction_id', v_transaction_id,
        'balance_before', v_balance_before,
        'balance_after', v_balance_after,
        'from_allowance', p_from_allowance,
        'from_purchased', p_from_purchased
    );
END;
$$;

GRANT EXECUTE ON FUNCTION deduct_credits_atomic TO authenticated;
GRANT EXECUTE ON FUNCTION deduct_credits_atomic TO service_role;
```

- [ ] **Step 2: Apply migration to staging/dev**

Run: `supabase db push` (or `supabase migration up`)
Verify: RPC is callable in Supabase SQL editor.

- [ ] **Step 3: Commit migration**

```bash
git add supabase/migrations/20260316000001_atomic_credit_deduction.sql
git commit -m "feat: add atomic credit deduction RPC (balance + log in one transaction)"
```

---

### Task 5: Update deduct_credits to use atomic RPC with fallback

**Files:**
- Modify: `src/db/users.py:806-888`
- Create: `tests/db/test_credit_atomicity.py`

- [ ] **Step 1: Write failing tests for RPC integration**

Create `tests/db/test_credit_atomicity.py` with tests for:
- `test_deduct_credits_calls_atomic_rpc` — verifies RPC is called as primary path
- `test_deduct_credits_falls_back_when_rpc_unavailable` — verifies fallback to separate update+log
- `test_deduct_credits_rollback_on_log_failure` — verifies balance rollback when fallback log fails

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/db/test_credit_atomicity.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite deduct_credits to use RPC with fallback**

Replace `src/db/users.py` lines 806-888 with:
1. Try `deduct_credits_atomic` RPC first (atomic path)
2. If RPC succeeds with `success: true` — return
3. If RPC returns `concurrent_modification` — re-fetch balance, raise ValueError with helpful message
4. If RPC fails (exception) — fall through to legacy path
5. Legacy path: optimistic lock UPDATE, then log_credit_transaction
6. If log fails: ROLL BACK balance update, raise RuntimeError

Key change: line 868-874 currently says "Don't raise here — credits were already deducted." The new code instead rolls back the balance and raises, so no ghost deductions occur.

- [ ] **Step 4: Run atomicity tests**

Run: `pytest tests/db/test_credit_atomicity.py -v`
Expected: All PASS

- [ ] **Step 5: Run ALL credit-related tests for regression**

Run: `pytest tests/db/test_credit_transactions.py tests/services/test_credit_handler.py tests/routes/test_credits.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/db/users.py tests/db/test_credit_atomicity.py
git commit -m "fix: atomic credit deduction via RPC with rollback fallback

Closes KR1 of #2058"
```

---

## Chunk 4: KR3 — Refund Path Clarity & KR5 — Test Coverage

### Task 6: Test the no-deduction-on-error guarantee

**Files:**
- Create: `tests/services/test_refund_paths.py`

- [ ] **Step 1: Write tests verifying no-deduction-on-error behavior**

Tests verify:
- Non-streaming: `_handle_credits_and_usage` only called on success path (after provider break)
- Streaming: background task only created AFTER `create_done_sse()`
- `credit_deduction_failures` migration exists for reconciliation
- `TransactionType.REFUND` exists for manual admin refunds

- [ ] **Step 2: Run and verify all pass**

Run: `pytest tests/services/test_refund_paths.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_refund_paths.py
git commit -m "test: codify no-deduction-on-error guarantee

Closes KR3 of #2058"
```

---

### Task 7: Add remaining billing integration tests

**Files:**
- Create: `tests/services/test_billing_integration.py`

Tests cover CM Unit Test Coverage Section 6 gaps:
- Subscription allowance consumed before purchased credits
- Insufficient credits rejected before any DB write
- Free model zero cost
- Zero tokens skips deduction
- Async/sync cost calculation parity

- [ ] **Step 1: Write tests**
- [ ] **Step 2: Run and verify all pass**

Run: `pytest tests/services/test_billing_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_billing_integration.py
git commit -m "test: add billing integration tests for credit system coverage gaps

Closes KR5 of #2058"
```

---

## Verification Checklist

```bash
pytest tests/services/test_pricing_precheck.py \
       tests/db/test_trial_config_consistency.py \
       tests/db/test_credit_atomicity.py \
       tests/services/test_refund_paths.py \
       tests/services/test_billing_integration.py \
       tests/services/test_pricing.py \
       tests/services/test_credit_handler.py \
       tests/db/test_credit_transactions.py \
       tests/routes/test_credits.py \
       -v --tb=short
```

---

## Summary

| Task | KR | What Changes | Tests Added | Effort |
|------|-----|-------------|-------------|--------|
| 1 | KR2 | Consolidate patterns to config | 1 test | Small |
| 2 | KR2 | Wire `get_model_pricing_async()` before provider call | 2 tests | Small (highest ROI) |
| 3 | KR4 | Trial config uses `usage_limits.py` constants | 4 tests | Small |
| 4-5 | KR1 | Atomic RPC migration + rollback fallback | 3 tests | Medium |
| 6 | KR3 | Codify no-deduction-on-error guarantee | 4 tests | Small |
| 7 | KR5 | Billing integration coverage gaps | 5 tests | Small |
| **Total** | **5 KRs** | **1 migration, 6 files modified** | **19 tests** | |
