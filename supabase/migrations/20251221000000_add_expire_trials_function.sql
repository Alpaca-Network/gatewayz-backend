-- Add database function to update expired trial statuses
-- This function can be called from scheduled jobs or manually
-- Created: 2024-12-21

-- Function to update expired trials
CREATE OR REPLACE FUNCTION public.update_expired_trials()
RETURNS TABLE(
    users_updated INTEGER,
    api_keys_updated INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_users_updated INTEGER;
    v_api_keys_updated INTEGER;
    v_now TIMESTAMPTZ;
BEGIN
    v_now := NOW();

    -- Update users table for expired trials
    UPDATE users
    SET
        subscription_status = 'expired',
        updated_at = v_now
    WHERE
        subscription_status = 'trial'
        AND trial_expires_at < v_now
        AND trial_expires_at IS NOT NULL;

    GET DIAGNOSTICS v_users_updated = ROW_COUNT;

    -- Update api_keys_new table for users with expired trials
    UPDATE api_keys_new
    SET
        subscription_status = 'expired',
        trial_active = FALSE,
        trial_expired = TRUE,
        updated_at = v_now
    WHERE
        user_id IN (
            SELECT id
            FROM users
            WHERE subscription_status = 'expired'
                AND trial_expires_at < v_now
                AND trial_expires_at IS NOT NULL
        )
        AND is_trial = TRUE
        AND subscription_status != 'expired';

    GET DIAGNOSTICS v_api_keys_updated = ROW_COUNT;

    -- Log the update
    RAISE NOTICE 'Updated % users and % API keys with expired trials', v_users_updated, v_api_keys_updated;

    -- Return results
    RETURN QUERY SELECT v_users_updated, v_api_keys_updated;
END;
$$;

-- Add comment
COMMENT ON FUNCTION public.update_expired_trials() IS
'Updates subscription_status to expired for users and API keys where trial_expires_at has passed. Returns count of updated users and API keys.';

-- Grant execute permissions
GRANT EXECUTE ON FUNCTION public.update_expired_trials() TO authenticated;
GRANT EXECUTE ON FUNCTION public.update_expired_trials() TO service_role;
