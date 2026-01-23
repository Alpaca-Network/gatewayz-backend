-- Migration: Add Butter.dev Cache Support
-- Description: Adds user preferences column for cache settings and metadata column
--              for tracking cache hits in chat_completion_requests

-- ============================================================================
-- 1. Add preferences column to users table for storing user settings like cache
-- ============================================================================

-- Add preferences JSONB column to users table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'preferences'
    ) THEN
        ALTER TABLE public.users
        ADD COLUMN preferences JSONB DEFAULT '{}'::jsonb;

        COMMENT ON COLUMN public.users.preferences IS
            'User preferences including cache settings. Example: {"enable_butter_cache": true}';
    END IF;
END $$;

-- Create GIN index for fast JSONB queries on preferences
CREATE INDEX IF NOT EXISTS idx_users_preferences
ON public.users USING GIN (preferences);

-- ============================================================================
-- 2. Add metadata column to chat_completion_requests for cache tracking
-- ============================================================================

-- Add metadata JSONB column to chat_completion_requests if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'chat_completion_requests'
        AND column_name = 'metadata'
    ) THEN
        ALTER TABLE public.chat_completion_requests
        ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb;

        COMMENT ON COLUMN public.chat_completion_requests.metadata IS
            'Request metadata including cache info. Example: {"butter_cache_hit": true, "actual_cost_usd": 0.001}';
    END IF;
END $$;

-- Create index for querying cache hits efficiently
CREATE INDEX IF NOT EXISTS idx_chat_requests_metadata_cache_hit
ON public.chat_completion_requests ((metadata->>'butter_cache_hit'))
WHERE metadata->>'butter_cache_hit' IS NOT NULL;

-- Create partial index for analyzing cache performance by model
CREATE INDEX IF NOT EXISTS idx_chat_requests_cache_model_analysis
ON public.chat_completion_requests (model_id, created_at)
WHERE metadata->>'butter_cache_hit' = 'true';

-- ============================================================================
-- 3. Create view for cache analytics
-- ============================================================================

CREATE OR REPLACE VIEW public.butter_cache_analytics AS
SELECT
    m.model_name,
    p.name AS provider_name,
    p.slug AS provider_slug,
    COUNT(*) FILTER (WHERE ccr.metadata->>'butter_cache_hit' = 'true') AS cache_hits,
    COUNT(*) FILTER (WHERE ccr.metadata->>'butter_cache_hit' IS NULL OR ccr.metadata->>'butter_cache_hit' = 'false') AS cache_misses,
    COUNT(*) AS total_requests,
    ROUND(
        COUNT(*) FILTER (WHERE ccr.metadata->>'butter_cache_hit' = 'true')::numeric /
        NULLIF(COUNT(*), 0) * 100,
        2
    ) AS cache_hit_rate_percent,
    COALESCE(SUM(
        CASE
            WHEN ccr.metadata->>'butter_cache_hit' = 'true'
            THEN (ccr.metadata->>'actual_cost_usd')::numeric
            ELSE 0
        END
    ), 0) AS total_savings_usd,
    COALESCE(SUM(ccr.cost_usd), 0) AS total_charged_cost_usd
FROM public.chat_completion_requests ccr
JOIN public.models m ON ccr.model_id = m.id
JOIN public.providers p ON m.provider_id = p.id
WHERE ccr.status = 'completed'
    AND ccr.created_at >= NOW() - INTERVAL '30 days'
GROUP BY m.model_name, p.name, p.slug
ORDER BY total_requests DESC;

COMMENT ON VIEW public.butter_cache_analytics IS
    'Analytics view for Butter.dev cache performance by model over the last 30 days';

-- ============================================================================
-- 4. Create function for user cache preference lookup (optimized)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.get_user_cache_preference(p_user_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_enabled BOOLEAN;
BEGIN
    SELECT COALESCE((preferences->>'enable_butter_cache')::boolean, false)
    INTO v_enabled
    FROM public.users
    WHERE id = p_user_id;

    RETURN COALESCE(v_enabled, false);
END;
$$;

COMMENT ON FUNCTION public.get_user_cache_preference IS
    'Returns whether Butter.dev caching is enabled for a user. Defaults to false if not set.';

-- ============================================================================
-- 5. Create function to get cache savings for a user
-- ============================================================================

CREATE OR REPLACE FUNCTION public.get_user_cache_savings(
    p_user_id BIGINT,
    p_days INTEGER DEFAULT 30
)
RETURNS TABLE (
    total_requests BIGINT,
    cache_hits BIGINT,
    cache_misses BIGINT,
    cache_hit_rate_percent NUMERIC,
    total_savings_usd NUMERIC,
    estimated_monthly_savings_usd NUMERIC
)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_total BIGINT;
    v_hits BIGINT;
    v_savings NUMERIC;
BEGIN
    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE metadata->>'butter_cache_hit' = 'true'),
        COALESCE(SUM(
            CASE
                WHEN metadata->>'butter_cache_hit' = 'true'
                THEN (metadata->>'actual_cost_usd')::numeric
                ELSE 0
            END
        ), 0)
    INTO v_total, v_hits, v_savings
    FROM public.chat_completion_requests
    WHERE user_id = p_user_id
        AND status = 'completed'
        AND created_at >= NOW() - (p_days || ' days')::interval;

    RETURN QUERY SELECT
        v_total AS total_requests,
        v_hits AS cache_hits,
        (v_total - v_hits) AS cache_misses,
        CASE
            WHEN v_total > 0 THEN ROUND(v_hits::numeric / v_total * 100, 2)
            ELSE 0
        END AS cache_hit_rate_percent,
        ROUND(v_savings, 6) AS total_savings_usd,
        CASE
            WHEN p_days > 0 THEN ROUND(v_savings * 30 / p_days, 2)
            ELSE 0
        END AS estimated_monthly_savings_usd;
END;
$$;

COMMENT ON FUNCTION public.get_user_cache_savings IS
    'Returns cache performance statistics for a user over the specified number of days';
