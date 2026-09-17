-- Migration: redeem_coupon() -- the atomic check-and-grant for coupon redemption.
--
-- WHY A NEW FUNCTION RATHER THAN is_coupon_redeemable()
-- =====================================================
-- 20251009040000_add_coupon_system.sql shipped is_coupon_redeemable(code, user_id).
-- It is a *read-only validator*: it inspects the coupon and returns a verdict, and
-- it increments nothing. Redeeming through it means
--
--     verdict := is_coupon_redeemable(...);       -- transaction A
--     if verdict.is_valid then                     -- ... time passes ...
--         insert coupon_redemptions; update times_used   -- transaction B
--
-- which is a textbook TOCTOU. Two users redeeming a max_uses=1 global coupon at the
-- same moment both read times_used=0, both get is_valid=true, and both grant. The
-- `times_used_within_limit` CHECK does not save this, because PostgREST cannot
-- express `times_used = times_used + 1`: the Python client reads 0 and writes 1
-- twice, so the counter never exceeds max_uses and the CHECK never fires while the
-- money goes out twice. That is the bug this migration exists to make impossible.
--
-- is_coupon_redeemable() is therefore kept as-is (it is the honest name for what it
-- does, and a preview/"can I use this code" surface can still use it), and this
-- function does check-and-increment inside ONE transaction, under a row lock.
--
-- THE MECHANISM
-- =============
--   SELECT ... FROM coupons WHERE UPPER(code) = UPPER($1) FOR UPDATE
--
-- is the serialization point. Concurrent redemptions of the SAME coupon queue on
-- that lock; under READ COMMITTED the waiter re-reads the committed row when the
-- lock is released, so it sees the incremented times_used and is refused with
-- MAX_USES_EXCEEDED. Redemptions of *different* coupons never contend.
--
-- Three independent layers guard the money, in order of who catches what:
--   1. the row lock above          -- serializes; makes the counter check correct
--   2. uq_coupon_user              -- UNIQUE(coupon_id, user_id): one user cannot
--                                     redeem one coupon twice, enforced by the index
--   3. times_used_within_limit     -- CHECK(times_used <= max_uses): the ceiling,
--                                     enforced by Postgres on the increment
-- plus a fourth, inherited from the grant path: atomic_add_credits is called with a
-- DETERMINISTIC request_id derived from (coupon_id, user_id), so the partial-unique
-- index idx_credit_transactions_request_id (20260223000001) refuses a second credit
-- transaction for the same logical redemption even if layers 1-3 were all removed.
--
-- Layers 2, 3 and 4 are backstops, not the primary mechanism. They exist because a
-- coupon is money and the primary mechanism is one `FOR UPDATE` away from wrong.
--
-- LEDGER CONSISTENCY
-- ==================
-- The coupon_redemptions row, the coupons.times_used increment and the
-- credit_transactions row are written in this one function, therefore in one
-- transaction. There is no interleaving in which a user is credited without a
-- redemption row, or a redemption row exists without the credit -- the pairing that
-- would otherwise become a reconciliation problem nobody can settle later.
--
-- Verified against prod (ynleroehyrmaafkgjgmr) on 2026-09-16, from the live schema
-- rather than from this repo's migration history: both tables' columns come from
-- PostgREST's OpenAPI document, and every constraint named above was confirmed
-- present by probing it with a statement engineered to abort either way (a CHECK
-- violation if the constraint exists, an FK violation against a non-existent user
-- id if it does not) -- no rows were written.
--
-- Returns JSONB:
--   { "success": bool,
--     "error_code": text|null,     -- COUPON_NOT_FOUND | COUPON_INACTIVE |
--                                     COUPON_NOT_YET_ACTIVE | COUPON_EXPIRED |
--                                     COUPON_NOT_ASSIGNED | MAX_USES_EXCEEDED |
--                                     ALREADY_REDEEMED | USER_NOT_FOUND |
--                                     REDEMPTION_FAILED
--     "error_message": text|null,  -- safe to show a user
--     "debug": text|null,          -- server-side only; never returned to a caller
--     "coupon_id": bigint|null, "code": text|null, "value_applied": numeric|null,
--     "balance_before": numeric|null, "balance_after": numeric|null,
--     "redemption_id": bigint|null, "transaction_id": bigint|null }

-- ============================================================================
-- PRE-CREATE CLEANUP (make migration idempotent)
-- ============================================================================
DROP FUNCTION IF EXISTS public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT);

-- ============================================================================
-- FUNCTION DEFINITION
-- ============================================================================
CREATE OR REPLACE FUNCTION public.redeem_coupon(
    p_coupon_code VARCHAR(50),
    p_user_id     BIGINT,
    p_ip_address  VARCHAR(45) DEFAULT NULL,
    p_user_agent  TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
-- Pinned so a caller-controlled search_path cannot shadow `coupons`, `users` or
-- `atomic_add_credits` with objects of its own inside a SECURITY DEFINER body.
SET search_path = public, pg_temp
AS $$
DECLARE
    v_coupon          public.coupons%ROWTYPE;
    v_code            VARCHAR(50);
    v_now             TIMESTAMPTZ := NOW();
    v_grant           JSONB;
    v_request_id      UUID;
    v_balance_before  NUMERIC(10,2);
    v_balance_after   NUMERIC(10,2);
    v_redemption_id   BIGINT;
    v_constraint      TEXT;
BEGIN
    -- ======================================================================
    -- STEP 0: Argument sanity. These are "the caller sent nonsense" cases,
    -- kept separate from "the coupon is not redeemable" cases below.
    -- ======================================================================
    IF p_user_id IS NULL THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'USER_NOT_FOUND',
            'error_message', 'No account was supplied for this redemption.',
            'debug', 'p_user_id is null'
        );
    END IF;

    v_code := btrim(COALESCE(p_coupon_code, ''));
    IF v_code = '' THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_NOT_FOUND',
            'error_message', 'Invalid coupon code.', 'debug', 'empty code'
        );
    END IF;

    -- The FK on coupon_redemptions.user_id would catch this at the insert, but
    -- only as an opaque 23503 after the credit grant had already been attempted.
    PERFORM 1 FROM public.users WHERE id = p_user_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'USER_NOT_FOUND',
            'error_message', 'Account not found.',
            'debug', format('no users row for id=%s', p_user_id)
        );
    END IF;

    -- ======================================================================
    -- STEP 1: Take the coupon row lock. Everything after this point is
    -- serialized per coupon, which is what makes the times_used check below
    -- a decision rather than a guess.
    --
    -- Matched on UPPER(code) to agree with is_coupon_redeemable() and with
    -- idx_coupons_code_upper.
    --
    -- ORDER BY id LIMIT 1 is belt and braces. 20260917020000 adds
    -- UNIQUE (UPPER(code)), so at most one row can match and the ordering is a
    -- no-op. It is here so this function is not INDEPENDENTLY fragile: without
    -- it, and with a collision present, the row returned is whatever the plan
    -- yields first, and a VACUUM FULL rewrites the heap in index order and
    -- flips it. Measured: the same user redeemed one typed code on two
    -- consecutive days and was paid from two different coupon rows, because
    -- uq_coupon_user compares coupon_id and the ids differed. Pinning the
    -- oldest matching row makes that a single repeatable answer instead --
    -- still the wrong data, but no longer a double-grant path.
    -- ======================================================================
    SELECT * INTO v_coupon
    FROM public.coupons
    WHERE UPPER(code) = UPPER(v_code)
    ORDER BY id
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_NOT_FOUND',
            'error_message', 'Invalid coupon code.', 'debug', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 2: Eligibility. The order of these checks is deliberately the same
    -- as is_coupon_redeemable()'s, so a preview and an actual redemption can
    -- never give a user two different reasons for the same coupon. Each reason
    -- is a distinct error_code: "invalid coupon" for all of them is the exact
    -- masking this codebase spent the week removing.
    -- ======================================================================
    IF v_coupon.is_active = false THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_INACTIVE',
            'error_message', 'This coupon is no longer available.',
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    IF v_now < v_coupon.valid_from THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_NOT_YET_ACTIVE',
            'error_message', format('This coupon is not valid until %s.',
                                    to_char(v_coupon.valid_from, 'YYYY-MM-DD')),
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    IF v_now > v_coupon.valid_until THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_EXPIRED',
            'error_message', format('This coupon expired on %s.',
                                    to_char(v_coupon.valid_until, 'YYYY-MM-DD')),
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    -- A user_specific coupon belongs to exactly one account. The
    -- user_specific_must_have_user CHECK guarantees assigned_to_user_id is
    -- non-null for this scope, so this comparison is never NULL-vs-value.
    IF v_coupon.coupon_scope = 'user_specific'
       AND v_coupon.assigned_to_user_id IS DISTINCT FROM p_user_id THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'COUPON_NOT_ASSIGNED',
            'error_message', 'This coupon is not valid for your account.',
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    -- Deliberate divergence from is_coupon_redeemable(), which gates this
    -- check on `coupon_scope = 'global'`. For a user_specific coupon max_uses
    -- is pinned to 1 by the user_specific_max_uses CHECK and uq_coupon_user
    -- already allows the assigned user only one redemption, so the two agree
    -- in practice -- but a ceiling that is only enforced for some scopes is
    -- one schema change away from not being a ceiling. Enforce it for all.
    IF v_coupon.times_used >= v_coupon.max_uses THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'MAX_USES_EXCEEDED',
            'error_message', 'This coupon has reached its redemption limit.',
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.coupon_redemptions
        WHERE coupon_id = v_coupon.id AND user_id = p_user_id
    ) THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'ALREADY_REDEEMED',
            'error_message', 'You have already redeemed this coupon.',
            'debug', NULL, 'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    -- ======================================================================
    -- STEP 3: Grant the credits.
    --
    -- Reuses atomic_add_credits rather than touching users/credit_transactions
    -- here: it owns the FOR UPDATE on the user row, the purchased-vs-allowance
    -- split and the ledger row shape, and duplicating any of that would be a
    -- second implementation of the balance rules to keep in sync.
    --
    -- Target is 'purchased' -- coupon credits must not be forfeited when a
    -- subscription is cancelled, which is exactly what subscription_allowance
    -- means. src/db/credit_transactions.py::add_credits already documents
    -- purchased_credits as the destination for "admin/referral/coupon".
    --
    -- request_id is DETERMINISTIC in (coupon_id, user_id), not random: that is
    -- what makes the ledger's own unique index a redemption guard rather than
    -- just a retry guard. A second attempt at the same logical redemption comes
    -- back as idempotent=true instead of a second grant.
    -- ======================================================================
    v_request_id := md5(format('coupon_redemption:%s:%s', v_coupon.id, p_user_id))::UUID;

    v_grant := public.atomic_add_credits(
        p_user_id          := p_user_id,
        p_credits          := v_coupon.value_usd,
        p_transaction_type := 'coupon_redemption',
        p_description      := format('Coupon redeemed: %s', v_coupon.code),
        p_target           := 'purchased',
        p_payment_id       := NULL,
        p_metadata         := jsonb_build_object(
                                  'coupon_id',    v_coupon.id,
                                  'coupon_code',  v_coupon.code,
                                  'coupon_scope', v_coupon.coupon_scope::TEXT,
                                  'coupon_type',  v_coupon.coupon_type::TEXT
                              ),
        p_request_id       := v_request_id,
        p_created_by       := format('coupon:%s', v_coupon.id)
    );

    IF NOT COALESCE((v_grant->>'success')::BOOLEAN, false) THEN
        -- Raised, not returned: a RETURN here would commit the transaction,
        -- and we need the rollback. The handler below turns it into a
        -- structured REDEMPTION_FAILED.
        RAISE EXCEPTION 'coupon_grant_failed: %', COALESCE(v_grant->>'error', 'unknown');
    END IF;

    IF COALESCE((v_grant->>'idempotent')::BOOLEAN, false) THEN
        -- The ledger already holds this exact grant although coupon_redemptions
        -- does not. Only reachable if a redemption row was deleted while its
        -- credit transaction survived (or a coupon was hard-deleted and its id
        -- reused). Refusing is right: the money already went out once.
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'ALREADY_REDEEMED',
            'error_message', 'You have already redeemed this coupon.',
            'debug', 'credit ledger already holds this request_id; no redemption row',
            'coupon_id', v_coupon.id, 'code', v_coupon.code
        );
    END IF;

    -- ======================================================================
    -- STEP 4: The redemption ledger row.
    --
    -- Balances are derived from the grant's reported post-state and rounded to
    -- the columns' NUMERIC(10,2). `before` is computed BACKWARDS from the
    -- rounded `after` so that balance_change_matches_value
    -- (after = before + value_applied) holds by construction: value_usd is
    -- itself DECIMAL(10,2), so the subtraction is exact at that scale.
    -- Rounding the two independently would let a fractional-cent balance trip
    -- the CHECK and fail an otherwise-good redemption.
    --
    -- A balance past 99,999,999.99 overflows these columns and aborts the whole
    -- redemption (22003 -> REDEMPTION_FAILED). Loud and fully rolled back is
    -- the right failure: the alternative is a truncated financial record.
    -- ======================================================================
    v_balance_after  := ROUND((v_grant->>'new_balance')::NUMERIC, 2);
    v_balance_before := v_balance_after - v_coupon.value_usd;

    INSERT INTO public.coupon_redemptions (
        coupon_id, user_id, value_applied,
        user_balance_before, user_balance_after,
        ip_address, user_agent
    ) VALUES (
        v_coupon.id, p_user_id, v_coupon.value_usd,
        v_balance_before, v_balance_after,
        LEFT(p_ip_address, 45), p_user_agent
    )
    RETURNING id INTO v_redemption_id;

    -- ======================================================================
    -- STEP 5: The counter.
    --
    -- `times_used = times_used + 1` in ONE statement, not a value computed in
    -- Python and written back. Combined with times_used_within_limit this is
    -- what makes the ceiling a database guarantee rather than a hope.
    -- ======================================================================
    UPDATE public.coupons
    SET times_used = times_used + 1,
        updated_at = NOW()
    WHERE id = v_coupon.id;

    RETURN jsonb_build_object(
        'success', true, 'error_code', NULL, 'error_message', NULL, 'debug', NULL,
        'coupon_id', v_coupon.id,
        'code', v_coupon.code,
        'value_applied', v_coupon.value_usd,
        'balance_before', v_balance_before,
        'balance_after', v_balance_after,
        'redemption_id', v_redemption_id,
        'transaction_id', (v_grant->>'transaction_id')::BIGINT
    );

-- ==========================================================================
-- Every handler below rolls the ENTIRE body back -- the block carrying the
-- EXCEPTION clause is the whole function, so a failure at step 5 also undoes
-- the grant at step 3. Each one is matched on the constraint that actually
-- fired rather than on the SQLSTATE class: `check_violation` alone would map
-- balance_change_matches_value to "coupon exhausted", which is the kind of
-- confident-but-wrong reason this endpoint is built to avoid.
-- ==========================================================================
EXCEPTION
    WHEN unique_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint IN ('uq_coupon_user', 'idx_credit_transactions_request_id') THEN
            -- Lost a race that layer 1 should have prevented. The backstop held.
            RETURN jsonb_build_object(
                'success', false, 'error_code', 'ALREADY_REDEEMED',
                'error_message', 'You have already redeemed this coupon.',
                'debug', format('unique_violation on %s', v_constraint)
            );
        END IF;
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'REDEMPTION_FAILED',
            'error_message', 'Could not redeem this coupon. Please try again.',
            'debug', format('unexpected unique_violation on %s: %s',
                            COALESCE(v_constraint, '?'), SQLERRM)
        );

    WHEN check_violation THEN
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint = 'times_used_within_limit' THEN
            RETURN jsonb_build_object(
                'success', false, 'error_code', 'MAX_USES_EXCEEDED',
                'error_message', 'This coupon has reached its redemption limit.',
                'debug', 'check_violation on times_used_within_limit'
            );
        END IF;
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'REDEMPTION_FAILED',
            'error_message', 'Could not redeem this coupon. Please try again.',
            'debug', format('check_violation on %s: %s',
                            COALESCE(v_constraint, '?'), SQLERRM)
        );

    WHEN foreign_key_violation THEN
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'USER_NOT_FOUND',
            'error_message', 'Account not found.',
            'debug', format('foreign_key_violation: %s', SQLERRM)
        );

    WHEN OTHERS THEN
        -- SQLERRM goes in `debug`, which the API layer logs and never returns:
        -- a raw Postgres message on a user-facing endpoint leaks schema.
        RETURN jsonb_build_object(
            'success', false, 'error_code', 'REDEMPTION_FAILED',
            'error_message', 'Could not redeem this coupon. Please try again.',
            'debug', format('%s: %s', SQLSTATE, SQLERRM)
        );
END;
$$;

-- ============================================================================
-- PERMISSIONS
-- ============================================================================
-- This function moves money and is SECURITY DEFINER. PostgREST exposes every
-- function in the exposed schema as an RPC under the *caller's* role, so an
-- EXECUTE grant to `anon` or `authenticated` would be a public, unauthenticated
-- "give me credits" endpoint. Only the backend's service_role may call it; all
-- authorization for who may redeem is enforced above, on the coupon's scope.
REVOKE ALL ON FUNCTION public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT) FROM anon;
REVOKE ALL ON FUNCTION public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT) TO service_role;

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================
COMMENT ON FUNCTION public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT) IS
'Atomically redeems a coupon for a user: validates eligibility under a
SELECT ... FOR UPDATE on the coupon row, grants the credits via
atomic_add_credits with a deterministic request_id, writes the
coupon_redemptions ledger row and increments coupons.times_used -- all in one
transaction, so a granted credit without a redemption row (or the reverse) is
not representable. Returns a structured JSONB verdict with a distinct
error_code per failure reason; never raises to the caller.';

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- DROP FUNCTION IF EXISTS public.redeem_coupon(VARCHAR, BIGINT, VARCHAR, TEXT);
