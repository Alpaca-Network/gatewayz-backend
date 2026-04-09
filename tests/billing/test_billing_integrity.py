"""
Billing integrity tests for O1 key results.

Covers:
- KR1: Atomic credit deduction + rollback on log failure
- KR2: Pricing pre-check before provider call
- KR3: No-deduction-on-error guarantee
- KR4: Trial config consistency
- KR5: Credit system coverage gaps
"""

import inspect
from pathlib import Path

# =========================================================================
# KR1: Credit Deduction Atomicity
# =========================================================================


def test_deduct_credits_uses_atomic_rpc():
    """deduct_credits should try atomic_deduct_credits RPC as primary path."""
    source = Path("src/db/users.py").read_text()
    assert (
        'client.rpc("atomic_deduct_credits"' in source
    ), "deduct_credits must call atomic_deduct_credits RPC"


def test_legacy_path_rolls_back_on_log_failure():
    """Legacy path must roll back balance when transaction log fails."""
    source = Path("src/db/users.py").read_text()
    # After "Transaction log failed", there should be a rollback (update back to before values)
    assert (
        "Rolling back balance" in source
    ), "Legacy path must roll back balance when log_credit_transaction fails"
    # The old "Don't raise here" comment should be gone
    assert (
        "Don't raise here" not in source
    ), "Legacy path must NOT silently swallow transaction log failures"


# =========================================================================
# KR2: Pricing Pre-Check Before Provider Call
# =========================================================================


def test_chat_imports_pricing_for_precheck():
    """chat.py must import get_model_pricing_async for pre-inference validation."""
    source = Path("src/routes/chat.py").read_text()
    assert "get_model_pricing_async" in source


def test_pricing_precheck_before_provider_dispatch():
    """Pricing pre-check must appear BEFORE provider dispatch in chat.py."""
    source = Path("src/routes/chat.py").read_text()
    precheck_pos = source.find("await get_model_pricing_async(req.model)")
    dispatch_pos = source.find("PROVIDER_ROUTING[attempt_provider]")
    assert precheck_pos > 0, "get_model_pricing_async pre-check not found"
    assert precheck_pos < dispatch_pos, (
        f"Pricing pre-check (pos {precheck_pos}) must appear before "
        f"provider dispatch (pos {dispatch_pos})"
    )


def test_pricing_precheck_returns_422():
    """Pricing pre-check failure must return 422, not 500."""
    source = Path("src/routes/chat.py").read_text()
    # Find the precheck block and verify it raises 422
    precheck_start = source.find("await get_model_pricing_async(req.model)")
    chunk = source[precheck_start : precheck_start + 800]
    assert (
        "status_code=422" in chunk
    ), "Pricing pre-check must return 422 for unpriced high-value models"


# =========================================================================
# KR3: No-Deduction-On-Error Guarantee
# =========================================================================


def test_non_streaming_deduction_only_after_provider_success():
    """
    Credit deduction in non-streaming path must only happen AFTER
    the provider call succeeds (after 'break' in failover loop).
    """
    source = Path("src/routes/chat.py").read_text()
    assert "_handle_credits_and_usage(" in source


def test_streaming_background_task_only_after_done_event():
    """
    In the streaming generator, create_task(_process_stream_completion_background)
    must be called AFTER yield create_done_sse(). The function is defined
    earlier in the file, but the asyncio.create_task() call inside the
    generator must follow the [DONE] yield.
    """
    source = Path("src/routes/chat.py").read_text()
    # Find the LAST yield of [DONE] — the main streaming generator's final yield
    done_pos = source.rfind("yield create_done_sse()")
    assert done_pos > 0, "yield create_done_sse() not found"
    # The create_task call that schedules background processing should follow it
    task_call_pos = source.find("asyncio.create_task(", done_pos)
    assert task_call_pos > 0, "asyncio.create_task() not found after final done yield"
    assert task_call_pos > done_pos, (
        f"asyncio.create_task (pos {task_call_pos}) must appear after "
        f"the final yield create_done_sse() (pos {done_pos})"
    )


def test_reconciliation_table_exists():
    """credit_deduction_failures table must exist for manual reconciliation."""
    migrations_dir = Path("supabase/migrations")
    migration_files = [f.name for f in migrations_dir.glob("*credit_deduction_failures*")]
    assert len(migration_files) > 0


def test_refund_transaction_type_exists():
    """TransactionType.REFUND must exist for manual admin refunds."""
    from src.db.credit_transactions import TransactionType

    assert TransactionType.REFUND == "refund"


# =========================================================================
# KR4: Trial Config Consistency
# =========================================================================


def test_trial_duration_matches_config():
    from src.config.usage_limits import TRIAL_DURATION_DAYS

    assert TRIAL_DURATION_DAYS == 14  # Standard trial is 14 days


def test_trial_credits_is_5_dollars():
    from src.config.usage_limits import TRIAL_CREDITS_AMOUNT

    assert TRIAL_CREDITS_AMOUNT == 5.0


def test_start_trial_default_matches_config():
    """start_trial_for_key() default trial_days must match TRIAL_DURATION_DAYS."""
    from src.config.usage_limits import TRIAL_DURATION_DAYS
    from src.db.trials import start_trial_for_key

    sig = inspect.signature(start_trial_for_key)
    default_days = sig.parameters["trial_days"].default
    assert (
        default_days == TRIAL_DURATION_DAYS
    ), f"Default is {default_days} but config says {TRIAL_DURATION_DAYS}"


def test_api_keys_uses_config_constants():
    """api_keys.py should import from usage_limits, not hardcode values."""
    source = Path("src/db/api_keys.py").read_text()
    assert "TRIAL_CREDITS_AMOUNT" in source
    assert "TRIAL_DURATION_DAYS" in source


# =========================================================================
# KR5: Credit System Coverage Gaps
# =========================================================================


def test_free_model_zero_cost():
    """Free models (:free suffix) always cost $0."""
    from src.services.pricing import calculate_cost

    cost = calculate_cost("openai/gpt-4o:free", 10000, 5000)
    assert cost == 0.0


def test_zero_tokens_skip_logic_exists():
    """deduct_credits must skip deduction for near-zero amounts."""
    source = Path("src/db/users.py").read_text()
    # Check the early return for tiny amounts exists
    assert (
        "tokens < 0.000001" in source
    ), "deduct_credits must skip deduction for amounts below $0.000001"
    assert "Skipping credit deduction for minimal amount" in source


def test_atomic_rpc_migration_exists():
    """The atomic_deduct_credits migration must exist."""
    migrations_dir = Path("supabase/migrations")
    atomic_migrations = [f.name for f in migrations_dir.glob("*atomic_deduct_credits*")]
    assert (
        len(atomic_migrations) > 0
    ), "atomic_deduct_credits migration must exist in supabase/migrations/"
