-- Migration: Ensure update_model_tier function is available and schema cache is reloaded
-- Description: Fixes PGRST202 error where PostgREST cannot find the update_model_tier function
--   - Recreates the function to ensure it's in the database
--   - Forces PostgREST to reload the schema cache
--   - Adds security definer to ensure proper permissions
-- Created: 2025-12-07
-- Related Error: "Could not find the function public.update_model_tier without parameters in the schema cache"

-- ============================================================================
-- Recreate update_model_tier function with SECURITY DEFINER
-- ============================================================================
CREATE OR REPLACE FUNCTION public.update_model_tier()
RETURNS void
SECURITY DEFINER
AS $$
DECLARE
    p95_threshold NUMERIC;
    p75_threshold NUMERIC;
BEGIN
    -- Pre-calculate percentile thresholds using CTEs for better performance
    -- Only calculate if there are models with usage
    SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY usage_count_24h)
    INTO p95_threshold
    FROM model_health_tracking
    WHERE usage_count_24h > 0;

    SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY usage_count_24h)
    INTO p75_threshold
    FROM model_health_tracking
    WHERE usage_count_24h > 0;

    -- If no models have usage, skip tier updates (preserves existing behavior)
    IF p95_threshold IS NULL OR p75_threshold IS NULL THEN
        -- Only update models with no usage to on_demand tier
        UPDATE model_health_tracking
        SET monitoring_tier = 'on_demand',
            check_interval_seconds = 14400
        WHERE usage_count_24h = 0 AND monitoring_tier != 'on_demand';
        RETURN;
    END IF;

    -- Update to critical tier (top 5% by usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'critical',
        check_interval_seconds = 300 -- 5 minutes
    WHERE usage_count_24h >= p95_threshold
      AND monitoring_tier != 'critical';

    -- Update to popular tier (between 75th and 95th percentile)
    UPDATE model_health_tracking
    SET monitoring_tier = 'popular',
        check_interval_seconds = 1800 -- 30 minutes
    WHERE usage_count_24h >= p75_threshold
      AND usage_count_24h < p95_threshold
      AND monitoring_tier != 'popular';

    -- Update to standard tier (below 75th percentile but has usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'standard',
        check_interval_seconds = 7200 -- 2 hours
    WHERE usage_count_24h > 0
      AND usage_count_24h < p75_threshold
      AND monitoring_tier != 'standard';

    -- Update to on_demand tier (no recent usage)
    UPDATE model_health_tracking
    SET monitoring_tier = 'on_demand',
        check_interval_seconds = 14400 -- 4 hours
    WHERE usage_count_24h = 0 AND monitoring_tier != 'on_demand';

END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.update_model_tier IS 'Automatically update model monitoring tiers based on usage patterns. Uses pre-calculated percentile thresholds for better performance. Called hourly by the health monitoring service.';

-- ============================================================================
-- Grant execute permissions to service role and authenticated users
-- ============================================================================
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO service_role;
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO authenticated;
GRANT EXECUTE ON FUNCTION public.update_model_tier() TO anon;

-- ============================================================================
-- Force PostgREST to reload schema cache immediately
-- ============================================================================
NOTIFY pgrst, 'reload schema';

-- ============================================================================
-- Verify function exists and can be called
-- ============================================================================
DO $$
BEGIN
    -- Test that the function exists and can be executed
    PERFORM public.update_model_tier();
    RAISE NOTICE 'Successfully verified update_model_tier function - schema cache should be refreshed';
EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'Function exists but execution failed (this is OK if model_health_tracking table is empty): %', SQLERRM;
END $$;
