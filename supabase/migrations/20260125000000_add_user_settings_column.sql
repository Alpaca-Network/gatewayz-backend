-- Migration: Add User Settings Column
-- Description: Adds settings JSONB column to users table for storing user-specific
--              configuration like auto top-up settings
-- Date: 2026-01-25

-- ============================================================================
-- 1. Add settings column to users table for storing user settings
-- ============================================================================

-- Add settings JSONB column to users table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'settings'
    ) THEN
        ALTER TABLE public.users
        ADD COLUMN settings JSONB DEFAULT '{}'::jsonb;

        COMMENT ON COLUMN public.users.settings IS
            'User settings including auto top-up configuration. Example: {"auto_topup_enabled": true, "auto_topup_threshold": 500, "auto_topup_amount": 2500}';
    END IF;
END $$;

-- Create GIN index for fast JSONB queries on settings
CREATE INDEX IF NOT EXISTS idx_users_settings
ON public.users USING GIN (settings);

-- ============================================================================
-- 2. Create function for getting user auto top-up settings
-- ============================================================================

CREATE OR REPLACE FUNCTION public.get_user_auto_topup_settings(p_user_id BIGINT)
RETURNS TABLE (
    auto_topup_enabled BOOLEAN,
    auto_topup_threshold INTEGER,
    auto_topup_amount INTEGER
)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE((settings->>'auto_topup_enabled')::boolean, false) AS auto_topup_enabled,
        COALESCE((settings->>'auto_topup_threshold')::integer, 0) AS auto_topup_threshold,
        COALESCE((settings->>'auto_topup_amount')::integer, 0) AS auto_topup_amount
    FROM public.users
    WHERE id = p_user_id;
END;
$$;

COMMENT ON FUNCTION public.get_user_auto_topup_settings IS
    'Returns auto top-up settings for a user. Threshold and amount are in cents.';

-- ============================================================================
-- 3. Create function to get users eligible for auto top-up
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
        AND (COALESCE(u.subscription_allowance, 0) + COALESCE(u.purchased_credits, 0)) * 100 <
            COALESCE((u.settings->>'auto_topup_threshold')::integer, 0);
END;
$$;

COMMENT ON FUNCTION public.get_users_needing_auto_topup IS
    'Returns users who have auto top-up enabled and whose balance is below their threshold. Used by the auto top-up cron job.';
