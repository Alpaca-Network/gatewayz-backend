-- Migration: Add atomic_deduct_credits stored procedure
-- This function performs credit deduction + transaction logging in a single atomic transaction.
--
-- Problem:
-- The Python code does two separate Supabase calls:
--   1. UPDATE users SET subscription_allowance = ..., purchased_credits = ...
--   2. INSERT INTO credit_transactions (...)
-- If step 2 fails, user credits are deducted but no audit record exists ("vanishing money").
--
-- Solution:
-- A PostgreSQL function that wraps both operations in a single transaction.
-- If either operation fails, the entire transaction rolls back automatically.
--
-- Parameters:
--   p_user_id          - The user whose credits to deduct
--   p_tokens_amount    - Total amount to deduct (positive number, e.g. 0.05)
--   p_from_allowance   - Portion to deduct from subscription_allowance
--   p_from_purchased   - Portion to deduct from purchased_credits
--   p_transaction_type - Type of transaction (e.g. 'api_usage')
--   p_description      - Human-readable description
--   p_metadata         - Optional JSONB metadata (model info, token counts, etc.)
--   p_request_id       - Optional UUID for idempotency / tracing
--
-- Returns JSONB:
--   {
--     "success": true/false,
--     "transaction_id": <bigint>,
--     "new_allowance": <numeric>,
--     "new_purchased": <numeric>,
--     "new_balance": <numeric>,
--     "error": <string or null>
--   }

-- ============================================================================
-- PRE-CREATE CLEANUP (make migration idempotent)
-- ============================================================================
DROP FUNCTION IF EXISTS atomic_deduct_credits(
    BIGINT, NUMERIC, NUMERIC, NUMERIC,
    VARCHAR, TEXT, JSONB, UUID
);

-- ============================================================================
-- FUNCTION DEFINITION
-- ============================================================================
CREATE OR REPLACE FUNCTION atomic_deduct_credits(
    p_user_id          BIGINT,
    p_tokens_amount    NUMERIC,
    p_from_allowance   NUMERIC,
    p_from_purchased   NUMERIC,
    p_transaction_type VARCHAR(50),
    p_description      TEXT,
    p_metadata         JSONB DEFAULT '{}'::JSONB,
    p_request_id       UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
AS $$
DECLARE
    v_allowance_before  NUMERIC;
    v_purchased_before  NUMERIC;
    v_allowance_after   NUMERIC;
    v_purchased_after   NUMERIC;
    v_balance_before    NUMERIC;
    v_balance_after     NUMERIC;
    v_transaction_id    BIGINT;
    v_updated_rows      INTEGER;
BEGIN
    -- ======================================================================
    -- VALIDATION
    -- ======================================================================
    IF p_tokens_amount <= 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'tokens_amount must be positive',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    IF p_from_allowance < 0 OR p_from_purchased < 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'from_allowance and from_purchased must be non-negative',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- Verify the breakdown adds up to the total (with small epsilon for floating point)
    IF ABS((p_from_allowance + p_from_purchased) - p_tokens_amount) > 0.000001 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'from_allowance + from_purchased must equal tokens_amount',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 1: UPDATE users with optimistic locking (row-level lock via FOR UPDATE)
    -- ======================================================================
    -- Lock the user row to prevent concurrent modifications, then read current balances.
    SELECT subscription_allowance, purchased_credits
    INTO v_allowance_before, v_purchased_before
    FROM users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'user_not_found',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- Coalesce NULLs to 0
    v_allowance_before := COALESCE(v_allowance_before, 0);
    v_purchased_before := COALESCE(v_purchased_before, 0);
    v_balance_before := v_allowance_before + v_purchased_before;

    -- Check sufficient balance
    IF v_balance_before < p_tokens_amount THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_credits',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    -- Check that each component has enough
    IF v_allowance_before < p_from_allowance THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_allowance',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    IF v_purchased_before < p_from_purchased THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_purchased_credits',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    -- Calculate new balances
    v_allowance_after := v_allowance_before - p_from_allowance;
    v_purchased_after := v_purchased_before - p_from_purchased;
    v_balance_after := v_allowance_after + v_purchased_after;

    -- Perform the update (row is already locked by FOR UPDATE)
    UPDATE users
    SET subscription_allowance = v_allowance_after,
        purchased_credits = v_purchased_after,
        updated_at = NOW()
    WHERE id = p_user_id;

    GET DIAGNOSTICS v_updated_rows = ROW_COUNT;

    IF v_updated_rows = 0 THEN
        -- This shouldn't happen since we locked the row, but handle it defensively
        RETURN jsonb_build_object(
            'success', false,
            'error', 'update_failed_unexpectedly',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 2: INSERT credit_transactions (same transaction as the UPDATE above)
    -- ======================================================================
    INSERT INTO credit_transactions (
        user_id,
        amount,
        transaction_type,
        description,
        balance_before,
        balance_after,
        metadata,
        created_at
    ) VALUES (
        p_user_id,
        -p_tokens_amount,  -- Negative for deduction
        p_transaction_type,
        p_description,
        v_balance_before,
        v_balance_after,
        -- Merge the caller-provided metadata with the breakdown details
        COALESCE(p_metadata, '{}'::JSONB) || jsonb_build_object(
            'from_allowance', p_from_allowance,
            'from_purchased', p_from_purchased,
            'allowance_before', v_allowance_before,
            'allowance_after', v_allowance_after,
            'purchased_before', v_purchased_before,
            'purchased_after', v_purchased_after,
            'atomic_procedure', true
        ) || CASE
            WHEN p_request_id IS NOT NULL
            THEN jsonb_build_object('request_id', p_request_id::TEXT)
            ELSE '{}'::JSONB
        END,
        NOW()
    )
    RETURNING id INTO v_transaction_id;

    -- ======================================================================
    -- SUCCESS: Both operations completed in the same transaction
    -- ======================================================================
    RETURN jsonb_build_object(
        'success', true,
        'transaction_id', v_transaction_id,
        'new_allowance', v_allowance_after,
        'new_purchased', v_purchased_after,
        'new_balance', v_balance_after,
        'error', NULL
    );

EXCEPTION
    WHEN OTHERS THEN
        -- Any unexpected error: the entire transaction is rolled back automatically
        -- (both the UPDATE and the INSERT are undone)
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
END;
$$;

-- ============================================================================
-- PERMISSIONS
-- ============================================================================
-- Only the service_role should be able to call this function.
-- The service_role is what the API backend uses via the Supabase client.
REVOKE ALL ON FUNCTION atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID) FROM anon;
REVOKE ALL ON FUNCTION atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID) FROM authenticated;
GRANT EXECUTE ON FUNCTION atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID) TO service_role;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON FUNCTION atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID) IS
'Atomically deducts credits from a user and logs the transaction in a single database transaction.
If either the balance update or the transaction log insert fails, both are rolled back.
Uses SELECT ... FOR UPDATE to prevent concurrent modification race conditions.
Returns JSONB with success status, new balances, and the transaction_id.';

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- To rollback this migration, run:
--
-- DROP FUNCTION IF EXISTS atomic_deduct_credits(BIGINT, NUMERIC, NUMERIC, NUMERIC, VARCHAR, TEXT, JSONB, UUID);
