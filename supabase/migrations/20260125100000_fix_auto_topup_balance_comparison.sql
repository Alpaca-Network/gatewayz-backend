-- Migration: Fix Auto Top-Up Balance Comparison
-- Description: Fixes the balance comparison in get_users_needing_auto_topup function.
--              The previous version incorrectly multiplied the balance by 100, but
--              since the database stores credits in dollars and thresholds in cents,
--              we need to divide the threshold by 100 instead.
-- Date: 2026-01-25

-- ============================================================================
-- Fix the get_users_needing_auto_topup function
-- ============================================================================

CREATE OR REPLACE FUNCTION public.get_users_needing_auto_topup()
RETURNS TABLE (
    user_id BIGINT,
    email TEXT,
    current_balance NUMERIC,
    auto_topup_threshold INTEGER,
    auto_topup_amount INTEGER,
    stripe_customer_id TEXT
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT
        u.id AS user_id,
        u.email,
        COALESCE(u.subscription_allowance, 0) + COALESCE(u.purchased_credits, 0) AS current_balance,
        COALESCE((u.settings->>'auto_topup_threshold')::integer, 0) AS auto_topup_threshold,
        COALESCE((u.settings->>'auto_topup_amount')::integer, 0) AS auto_topup_amount,
        u.stripe_customer_id
    FROM public.users u
    WHERE (u.settings->>'auto_topup_enabled')::boolean = true
        AND u.stripe_customer_id IS NOT NULL
        -- Compare balance (in dollars) to threshold (in cents converted to dollars)
        -- Example: balance $5.00 < threshold 1000 cents / 100 = $10.00 -> triggers top-up
        AND (COALESCE(u.subscription_allowance, 0) + COALESCE(u.purchased_credits, 0)) <
            COALESCE((u.settings->>'auto_topup_threshold')::integer, 0) / 100.0;
END;
$$;

COMMENT ON FUNCTION public.get_users_needing_auto_topup IS
    'Returns users who have auto top-up enabled and whose balance (in dollars) is below their threshold (stored in cents, converted to dollars). Used by the auto top-up cron job.';
