-- Migration: Add atomic_add_credits stored procedure
-- Mirror of atomic_deduct_credits (20260223000003) for the GRANT direction.
--
-- Problem:
-- The Python add_credits_to_user() did a non-atomic read-modify-write:
--   1. SELECT purchased_credits/subscription_allowance
--   2. compute new = before + credits
--   3. UPDATE users SET ...
--   4. INSERT INTO credit_transactions (...)
-- Two concurrent grants that read the same "before" lose one increment, and a
-- duplicate webhook delivery that slips past the payment-status guard grants the
-- credits twice. Deduction was already atomic + idempotent; granting was not.
--
-- Solution:
-- A PostgreSQL function that locks the user row (FOR UPDATE), performs the
-- balance update and the ledger insert in one transaction, and enforces
-- idempotency via the existing partial-unique index on
-- credit_transactions.request_id (idx_credit_transactions_request_id). If a
-- transaction with the same request_id already exists, the grant is skipped and
-- reported as an idempotent no-op.
--
-- Parameters:
--   p_user_id          - The user to credit
--   p_credits          - Amount to add (positive)
--   p_transaction_type - Type of transaction (e.g. 'purchase', 'first_topup_bonus')
--   p_description      - Human-readable description
--   p_target           - 'purchased' (default) or 'allowance' — which balance to credit
--   p_payment_id       - Optional payments.id FK to record on the ledger row
--   p_metadata         - Optional JSONB metadata
--   p_request_id       - Optional UUID idempotency key (unique per logical grant)
--   p_created_by       - Optional actor identifier (e.g. 'admin:123')
--
-- Returns JSONB:
--   { "success": bool, "idempotent": bool, "transaction_id": bigint|null,
--     "new_purchased": numeric, "new_allowance": numeric, "new_balance": numeric,
--     "error": text|null }

-- ============================================================================
-- PRE-CREATE CLEANUP (make migration idempotent)
-- ============================================================================
DROP FUNCTION IF EXISTS atomic_add_credits(
    BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT
);

-- ============================================================================
-- FUNCTION DEFINITION
-- ============================================================================
CREATE OR REPLACE FUNCTION atomic_add_credits(
    p_user_id          BIGINT,
    p_credits          NUMERIC,
    p_transaction_type VARCHAR(50),
    p_description      TEXT,
    p_target           VARCHAR(20) DEFAULT 'purchased',
    p_payment_id       BIGINT DEFAULT NULL,
    p_metadata         JSONB DEFAULT '{}'::JSONB,
    p_request_id       UUID DEFAULT NULL,
    p_created_by       TEXT DEFAULT NULL
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
    v_existing_id       BIGINT;
BEGIN
    -- ======================================================================
    -- VALIDATION
    -- ======================================================================
    IF p_credits <= 0 THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', 'credits must be positive',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    IF p_target NOT IN ('purchased', 'allowance') THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false,
            'error', 'target must be purchased or allowance',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 1: Lock the user row to serialize concurrent grants/deductions.
    -- ======================================================================
    SELECT subscription_allowance, purchased_credits
    INTO v_allowance_before, v_purchased_before
    FROM users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', 'user_not_found',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    v_allowance_before := COALESCE(v_allowance_before, 0);
    v_purchased_before := COALESCE(v_purchased_before, 0);
    v_balance_before := v_allowance_before + v_purchased_before;

    -- ======================================================================
    -- STEP 2: Idempotency check (while holding the user lock so duplicate
    -- deliveries for the same user serialize here rather than racing).
    -- ======================================================================
    IF p_request_id IS NOT NULL THEN
        SELECT id INTO v_existing_id
        FROM credit_transactions
        WHERE request_id = p_request_id
        LIMIT 1;

        IF FOUND THEN
            RETURN jsonb_build_object(
                'success', true, 'idempotent', true,
                'transaction_id', v_existing_id,
                'new_purchased', v_purchased_before,
                'new_allowance', v_allowance_before,
                'new_balance', v_balance_before,
                'error', NULL
            );
        END IF;
    END IF;

    -- ======================================================================
    -- STEP 3: Apply the grant to the requested balance.
    -- ======================================================================
    IF p_target = 'allowance' THEN
        v_allowance_after := v_allowance_before + p_credits;
        v_purchased_after := v_purchased_before;
    ELSE
        v_purchased_after := v_purchased_before + p_credits;
        v_allowance_after := v_allowance_before;
    END IF;
    v_balance_after := v_allowance_after + v_purchased_after;

    UPDATE users
    SET subscription_allowance = v_allowance_after,
        purchased_credits = v_purchased_after,
        updated_at = NOW()
    WHERE id = p_user_id;

    -- ======================================================================
    -- STEP 4: Ledger insert (same transaction). The partial-unique index on
    -- request_id is the final backstop against duplicates.
    -- ======================================================================
    INSERT INTO credit_transactions (
        user_id, amount, transaction_type, description,
        balance_before, balance_after, payment_id, request_id, metadata, created_at
    ) VALUES (
        p_user_id, p_credits, p_transaction_type, p_description,
        v_balance_before, v_balance_after, p_payment_id, p_request_id,
        COALESCE(p_metadata, '{}'::JSONB) || jsonb_build_object(
            'target', p_target,
            'allowance_before', v_allowance_before,
            'allowance_after', v_allowance_after,
            'purchased_before', v_purchased_before,
            'purchased_after', v_purchased_after,
            'atomic_procedure', true
        ) || CASE
            WHEN p_created_by IS NOT NULL
            THEN jsonb_build_object('created_by', p_created_by)
            ELSE '{}'::JSONB
        END,
        NOW()
    )
    RETURNING id INTO v_transaction_id;

    RETURN jsonb_build_object(
        'success', true, 'idempotent', false,
        'transaction_id', v_transaction_id,
        'new_purchased', v_purchased_after,
        'new_allowance', v_allowance_after,
        'new_balance', v_balance_after,
        'error', NULL
    );

EXCEPTION
    -- A concurrent grant with the same request_id that committed between our
    -- idempotency SELECT and the INSERT trips the unique index. Treat it as an
    -- idempotent no-op rather than an error (the credits were granted once).
    WHEN unique_violation THEN
        SELECT id INTO v_existing_id
        FROM credit_transactions
        WHERE request_id = p_request_id
        LIMIT 1;
        RETURN jsonb_build_object(
            'success', true, 'idempotent', true,
            'transaction_id', v_existing_id,
            'new_purchased', v_purchased_before,
            'new_allowance', v_allowance_before,
            'new_balance', v_balance_before,
            'error', NULL
        );
    WHEN OTHERS THEN
        -- Any other unexpected error rolls back the whole transaction.
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', SQLERRM,
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
END;
$$;

-- ============================================================================
-- PERMISSIONS
-- ============================================================================
REVOKE ALL ON FUNCTION atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT) FROM anon;
REVOKE ALL ON FUNCTION atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT) FROM authenticated;
GRANT EXECUTE ON FUNCTION atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT) TO service_role;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON FUNCTION atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT) IS
'Atomically adds credits to a user and logs the transaction in a single database
transaction. Uses SELECT ... FOR UPDATE to serialize concurrent balance changes,
and the partial-unique index on credit_transactions.request_id to make the grant
idempotent across duplicate webhook deliveries. Mirror of atomic_deduct_credits.';

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- DROP FUNCTION IF EXISTS atomic_add_credits(BIGINT, NUMERIC, VARCHAR, TEXT, VARCHAR, BIGINT, JSONB, UUID, TEXT);
