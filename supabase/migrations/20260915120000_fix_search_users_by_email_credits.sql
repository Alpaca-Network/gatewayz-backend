-- Migration: repair search_users_by_email after users.credits was dropped
-- Created: 2026-09-15
--
-- 20260417000000_drop_legacy_credits_column.sql dropped public.users.credits,
-- but search_users_by_email (20260105000001_add_search_users_function.sql) still
-- selected u.credits. Every call raised 42703 (undefined column), which made
-- GET /admin/users?email=... return 500 for every email-only search.
--
-- The balance now lives in subscription_allowance + purchased_credits. The
-- function keeps returning a `credits` column so the admin panel's response
-- contract is unchanged, and additionally returns the two components.

-- The RETURNS TABLE signature changes, so the old function must be dropped
-- first: CREATE OR REPLACE cannot change a function's return type.
DROP FUNCTION IF EXISTS search_users_by_email(TEXT, INTEGER, INTEGER);

CREATE OR REPLACE FUNCTION search_users_by_email(
    search_term TEXT,
    result_limit INTEGER DEFAULT 100,
    result_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    id BIGINT,
    username TEXT,
    email TEXT,
    subscription_allowance NUMERIC,
    purchased_credits NUMERIC,
    credits NUMERIC,
    is_active BOOLEAN,
    role TEXT,
    registration_date TIMESTAMP WITH TIME ZONE,
    auth_method TEXT,
    subscription_status TEXT,
    trial_expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE,
    total_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH counted AS (
        SELECT COUNT(*) as total
        FROM users
        WHERE users.email ILIKE '%' || search_term || '%'
    )
    SELECT
        u.id::BIGINT,
        u.username::TEXT,
        u.email::TEXT,
        COALESCE(u.subscription_allowance, 0)::NUMERIC,
        COALESCE(u.purchased_credits, 0)::NUMERIC,
        (COALESCE(u.subscription_allowance, 0) + COALESCE(u.purchased_credits, 0))::NUMERIC,
        u.is_active::BOOLEAN,
        u.role::TEXT,
        u.registration_date::TIMESTAMP WITH TIME ZONE,
        u.auth_method::TEXT,
        u.subscription_status::TEXT,
        u.trial_expires_at::TIMESTAMP WITH TIME ZONE,
        u.created_at::TIMESTAMP WITH TIME ZONE,
        u.updated_at::TIMESTAMP WITH TIME ZONE,
        c.total::BIGINT as total_count
    FROM users u
    CROSS JOIN counted c
    WHERE u.email ILIKE '%' || search_term || '%'
    ORDER BY u.created_at DESC
    LIMIT result_limit
    OFFSET result_offset;
END;
$$ LANGUAGE plpgsql STABLE;

-- Grants: service_role only.
--
-- The original function granted EXECUTE to `authenticated` and `anon`. That
-- predates the May-2026 emergency lockdown (RLS + revoked anon/authenticated
-- grants on `users` and friends, after the anon key was found to leak plaintext
-- API keys, emails and Stripe IDs -- see tests/security/test_rls_anon_lockdown.py).
-- This function reads `users` and returns email + balances, so re-granting it to
-- anon would hand that lockdown a function-shaped way around itself the moment
-- anyone adds SECURITY DEFINER. It has exactly one caller
-- (src/routes/admin.py, service-role key), so the grants are dropped rather than
-- restored. DROP FUNCTION above already removed the old ones; these REVOKEs are
-- belt-and-braces in case the function is recreated by an older migration replay.
REVOKE ALL ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) FROM anon;
REVOKE ALL ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) FROM authenticated;
GRANT EXECUTE ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) TO service_role;
