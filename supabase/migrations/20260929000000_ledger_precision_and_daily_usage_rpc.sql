-- Migration: ledger precision + daily-usage aggregate
--
-- credit_transactions.amount / balance_before / balance_after were numeric(10,2)
-- and users.subscription_allowance / purchased_credits were DECIMAL(10,4).
-- Sub-cent charges were stored as 0.00 in the ledger (production 2026-09-29:
-- 3955 of 4351 api_usage rows since July had amount = 0.00 while
-- metadata.cost_usd was real), so every ledger SUM, including the $1/day
-- limiter, was blind to them; and users balances silently dropped deductions
-- below ~$0.00005.
--
-- Widening numeric(p,s) -> numeric(14,8) is lossless for existing values and
-- does not need a table rewrite for the scale increase to be safe. Sign
-- convention is unchanged: deductions are NEGATIVE amounts. atomic_deduct_credits
-- / atomic_add_credits use unconstrained NUMERIC variables, so they need no
-- change. No view in supabase/migrations references these columns.
-- Idempotent: re-running ALTER TYPE to the same type is a no-op.

ALTER TABLE public.credit_transactions
    ALTER COLUMN amount         TYPE numeric(14,8),
    ALTER COLUMN balance_before TYPE numeric(14,8),
    ALTER COLUMN balance_after  TYPE numeric(14,8);

ALTER TABLE public.users
    ALTER COLUMN subscription_allowance TYPE numeric(14,8),
    ALTER COLUMN purchased_credits      TYPE numeric(14,8);

-- Server-side daily usage total: one aggregate, no PostgREST 1000-row cap.
CREATE OR REPLACE FUNCTION public.get_daily_usage_total(
    p_user_id BIGINT,
    p_since   TIMESTAMPTZ
)
RETURNS NUMERIC
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(SUM(-amount), 0)
    FROM credit_transactions
    WHERE user_id = p_user_id
      AND created_at >= p_since
      AND amount < 0;
$$;

REVOKE ALL ON FUNCTION public.get_daily_usage_total(BIGINT, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.get_daily_usage_total(BIGINT, TIMESTAMPTZ) FROM anon;
REVOKE ALL ON FUNCTION public.get_daily_usage_total(BIGINT, TIMESTAMPTZ) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.get_daily_usage_total(BIGINT, TIMESTAMPTZ) TO service_role;

COMMENT ON FUNCTION public.get_daily_usage_total(BIGINT, TIMESTAMPTZ) IS
'Sum of usage (negative credit_transactions.amount, returned positive) for a user since p_since.';

-- DOWN (manual): narrowing would round/overflow data; do not roll back the column types.
-- DROP FUNCTION IF EXISTS public.get_daily_usage_total(BIGINT, TIMESTAMPTZ);
