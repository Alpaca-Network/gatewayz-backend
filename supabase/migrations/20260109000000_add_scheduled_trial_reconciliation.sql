-- Add scheduled job to reconcile trial status for paid users
-- This ensures users who have paid (subscription or credits) are not marked as trial

-- Enable pg_cron extension if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Create the reconciliation function
CREATE OR REPLACE FUNCTION reconcile_paid_users_trial_status()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    pro_max_updated INTEGER := 0;
    subscription_updated INTEGER := 0;
    credit_purchase_updated INTEGER := 0;
    users_table_updated INTEGER := 0;
    result jsonb;
BEGIN
    -- 1. Clear trial status for Pro/Max tier users
    WITH updated AS (
        UPDATE api_keys_new
        SET
            is_trial = FALSE,
            trial_converted = TRUE,
            subscription_status = 'active',
            subscription_plan = u.tier,
            updated_at = NOW()
        FROM users u
        WHERE api_keys_new.user_id = u.id
            AND u.tier IN ('pro', 'max')
            AND api_keys_new.is_trial = TRUE
        RETURNING api_keys_new.id
    )
    SELECT COUNT(*) INTO pro_max_updated FROM updated;

    -- 2. Clear trial status for users with active Stripe subscriptions
    WITH updated AS (
        UPDATE api_keys_new
        SET
            is_trial = FALSE,
            trial_converted = TRUE,
            subscription_status = 'active',
            subscription_plan = COALESCE(u.tier, 'pro'),
            updated_at = NOW()
        FROM users u
        WHERE api_keys_new.user_id = u.id
            AND u.stripe_subscription_id IS NOT NULL
            AND u.subscription_status = 'active'
            AND api_keys_new.is_trial = TRUE
        RETURNING api_keys_new.id
    )
    SELECT COUNT(*) INTO subscription_updated FROM updated;

    -- 3. Clear trial status for users who have purchased credits
    WITH updated AS (
        UPDATE api_keys_new
        SET
            is_trial = FALSE,
            trial_converted = TRUE,
            subscription_status = 'active',
            subscription_plan = COALESCE(u.tier, 'basic'),
            updated_at = NOW()
        FROM users u
        WHERE api_keys_new.user_id = u.id
            AND api_keys_new.is_trial = TRUE
            AND EXISTS (
                SELECT 1 FROM credit_transactions ct
                WHERE ct.user_id = u.id
                AND ct.transaction_type = 'purchase'
            )
        RETURNING api_keys_new.id
    )
    SELECT COUNT(*) INTO credit_purchase_updated FROM updated;

    -- 4. Update users table subscription_status for consistency
    WITH updated AS (
        UPDATE users
        SET
            subscription_status = 'active',
            updated_at = NOW()
        WHERE subscription_status = 'trial'
            AND (
                tier IN ('pro', 'max')
                OR stripe_subscription_id IS NOT NULL
                OR EXISTS (
                    SELECT 1 FROM credit_transactions ct
                    WHERE ct.user_id = users.id
                    AND ct.transaction_type = 'purchase'
                )
            )
        RETURNING id
    )
    SELECT COUNT(*) INTO users_table_updated FROM updated;

    -- Build result JSON
    result := jsonb_build_object(
        'timestamp', NOW(),
        'pro_max_api_keys_updated', pro_max_updated,
        'subscription_api_keys_updated', subscription_updated,
        'credit_purchase_api_keys_updated', credit_purchase_updated,
        'users_table_updated', users_table_updated,
        'total_api_keys_updated', pro_max_updated + subscription_updated + credit_purchase_updated
    );

    -- Log the result (will appear in Supabase logs)
    RAISE NOTICE 'Trial reconciliation completed: %', result;

    RETURN result;
END;
$$;

-- Grant execute permission to service role
GRANT EXECUTE ON FUNCTION reconcile_paid_users_trial_status() TO service_role;

-- Create a table to log reconciliation runs (optional, for audit purposes)
CREATE TABLE IF NOT EXISTS reconciliation_logs (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL,
    result jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for querying logs
CREATE INDEX IF NOT EXISTS idx_reconciliation_logs_job_name_created
ON reconciliation_logs(job_name, created_at DESC);

-- Create a wrapper function that logs the result
CREATE OR REPLACE FUNCTION run_and_log_trial_reconciliation()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result jsonb;
BEGIN
    -- Run the reconciliation
    result := reconcile_paid_users_trial_status();

    -- Log the result
    INSERT INTO reconciliation_logs (job_name, result)
    VALUES ('trial_status_reconciliation', result);

    -- Only keep last 90 days of logs
    DELETE FROM reconciliation_logs
    WHERE job_name = 'trial_status_reconciliation'
    AND created_at < NOW() - INTERVAL '90 days';
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION run_and_log_trial_reconciliation() TO service_role;

-- Schedule the job to run daily at 3:00 AM UTC
-- Note: pg_cron jobs are stored in the cron schema
-- Wrapped in DO block to handle pg_cron extension not being available
DO $block$
BEGIN
    -- Check if pg_cron extension exists
    IF EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_cron'
    ) THEN
        -- Schedule the cron job
        PERFORM cron.schedule(
            'reconcile-paid-users-trial-status',  -- job name
            '0 3 * * *',                           -- cron schedule: daily at 3 AM UTC
            $$SELECT run_and_log_trial_reconciliation()$$
        );
        RAISE NOTICE 'Successfully scheduled trial reconciliation cron job';
    ELSE
        RAISE WARNING 'pg_cron extension not found. Scheduled trial reconciliation will not run automatically. Please enable pg_cron or run reconciliation manually.';
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE WARNING 'Failed to schedule trial reconciliation cron job: %. Job can be scheduled manually later.', SQLERRM;
END;
$block$;

-- Add a comment explaining the job
COMMENT ON FUNCTION reconcile_paid_users_trial_status() IS
'Reconciles trial status for users who have paid (Pro/Max tier, active subscription, or credit purchases).
Clears is_trial flag and sets subscription_status to active.
Scheduled to run daily via pg_cron.';

-- Run once immediately to catch any existing discrepancies
SELECT run_and_log_trial_reconciliation();
