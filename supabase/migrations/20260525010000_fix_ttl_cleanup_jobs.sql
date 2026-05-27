-- Follow-up to 20260525000000_add_ttl_cleanup_jobs.sql
--
-- Code review on PR #2118 surfaced six issues with the original migration:
--   #4  chat_completion_requests TTL of 30d destroys lifetime aggregates used by
--       model_usage_analytics view. Extend to 90d and add a per-day rollup table
--       so long-term analytics can be derived from aggregates instead.
--   #7  mark-expired-trials was reduced to daily, but the runtime
--       BLOCKED_SUBSCRIPTION_STATUSES gate depends on subscription_status being
--       flipped to 'expired' promptly. Restore hourly schedule.
--   #9  SECURITY DEFINER functions need SET search_path to prevent
--       search-path-shadowing attacks.
--   #10 run_and_log_ttl_cleanups had no exception handling — one cleanup
--       failure aborts the entire transaction and loses all 3 audit log rows.
--   #12 cleanup_rate_limit_alerts second DELETE missing WHERE resolved=FALSE,
--       so the comment ("30 days hard cap for unresolved") diverges from SQL.
--   #15 Both cron DO blocks lack EXCEPTION handlers — a permission denied on
--       cron.unschedule aborts the whole migration.

-- Per-day aggregate rollup so TTL on chat_completion_requests doesn't destroy
-- lifetime analytics. Populated by run_and_log_ttl_cleanups BEFORE deleting.
CREATE TABLE IF NOT EXISTS chat_completion_daily_aggregates (
    day DATE NOT NULL,
    model_id BIGINT,
    user_id BIGINT,
    request_count BIGINT NOT NULL DEFAULT 0,
    completed_count BIGINT NOT NULL DEFAULT 0,
    failed_count BIGINT NOT NULL DEFAULT 0,
    total_input_tokens BIGINT NOT NULL DEFAULT 0,
    total_output_tokens BIGINT NOT NULL DEFAULT 0,
    total_cost_usd NUMERIC(20, 8) NOT NULL DEFAULT 0,
    first_request_at TIMESTAMPTZ,
    last_request_at TIMESTAMPTZ,
    PRIMARY KEY (day, model_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_daily_agg_day ON chat_completion_daily_aggregates(day);
CREATE INDEX IF NOT EXISTS idx_chat_daily_agg_model ON chat_completion_daily_aggregates(model_id);
CREATE INDEX IF NOT EXISTS idx_chat_daily_agg_user ON chat_completion_daily_aggregates(user_id);

CREATE OR REPLACE FUNCTION rollup_chat_completion_requests()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    rollup_count BIGINT := 0;
    cutoff_date DATE := (NOW() - INTERVAL '90 days')::DATE;
BEGIN
    -- Roll up days that are about to be deleted, idempotently.
    -- We aggregate ALL days older than the TTL cutoff (in case prior rollups missed).
    WITH source AS (
        SELECT
            created_at::DATE AS day,
            model_id,
            user_id,
            COUNT(*) AS request_count,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed_count,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(cost_usd), 0) AS total_cost_usd,
            MIN(created_at) AS first_request_at,
            MAX(created_at) AS last_request_at
        FROM chat_completion_requests
        WHERE created_at < (cutoff_date + INTERVAL '1 day')
        GROUP BY 1, 2, 3
    ), upsert AS (
        INSERT INTO chat_completion_daily_aggregates AS agg (
            day, model_id, user_id,
            request_count, completed_count, failed_count,
            total_input_tokens, total_output_tokens, total_cost_usd,
            first_request_at, last_request_at
        )
        SELECT * FROM source
        ON CONFLICT (day, model_id, user_id) DO UPDATE
        SET
            request_count = EXCLUDED.request_count,
            completed_count = EXCLUDED.completed_count,
            failed_count = EXCLUDED.failed_count,
            total_input_tokens = EXCLUDED.total_input_tokens,
            total_output_tokens = EXCLUDED.total_output_tokens,
            total_cost_usd = EXCLUDED.total_cost_usd,
            first_request_at = LEAST(agg.first_request_at, EXCLUDED.first_request_at),
            last_request_at = GREATEST(agg.last_request_at, EXCLUDED.last_request_at)
        RETURNING 1
    )
    SELECT COUNT(*) INTO rollup_count FROM upsert;

    RETURN jsonb_build_object(
        'timestamp', NOW(),
        'rolled_up', rollup_count,
        'cutoff_date', cutoff_date,
        'table', 'chat_completion_daily_aggregates'
    );
END;
$$;

-- Recreate cleanup functions with SET search_path AND longer TTL on chat_completion_requests.
CREATE OR REPLACE FUNCTION cleanup_chat_completion_requests()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted_count BIGINT := 0;
BEGIN
    WITH d AS (
        DELETE FROM chat_completion_requests
        WHERE created_at < NOW() - INTERVAL '90 days'
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM d;

    RETURN jsonb_build_object(
        'timestamp', NOW(),
        'deleted', deleted_count,
        'table', 'chat_completion_requests'
    );
END;
$$;

CREATE OR REPLACE FUNCTION cleanup_model_health_history()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    deleted_count BIGINT := 0;
BEGIN
    WITH d AS (
        DELETE FROM model_health_history
        WHERE checked_at < NOW() - INTERVAL '7 days'
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM d;

    RETURN jsonb_build_object(
        'timestamp', NOW(),
        'deleted', deleted_count,
        'table', 'model_health_history'
    );
END;
$$;

CREATE OR REPLACE FUNCTION cleanup_rate_limit_alerts()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    resolved_deleted BIGINT := 0;
    unresolved_deleted BIGINT := 0;
BEGIN
    WITH d AS (
        DELETE FROM rate_limit_alerts
        WHERE resolved = TRUE
          AND created_at < NOW() - INTERVAL '1 day'
        RETURNING 1
    )
    SELECT COUNT(*) INTO resolved_deleted FROM d;

    WITH d AS (
        DELETE FROM rate_limit_alerts
        WHERE resolved = FALSE
          AND created_at < NOW() - INTERVAL '30 days'
        RETURNING 1
    )
    SELECT COUNT(*) INTO unresolved_deleted FROM d;

    RETURN jsonb_build_object(
        'timestamp', NOW(),
        'resolved_deleted', resolved_deleted,
        'unresolved_deleted', unresolved_deleted,
        'table', 'rate_limit_alerts'
    );
END;
$$;

-- Per-function exception handling so a single failure doesn't abort the whole job.
CREATE OR REPLACE FUNCTION run_and_log_ttl_cleanups()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- Rollup must run BEFORE delete to preserve aggregates.
    BEGIN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES ('ttl_rollup_chat_completion_requests', rollup_chat_completion_requests());
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES (
            'ttl_rollup_chat_completion_requests',
            jsonb_build_object('timestamp', NOW(), 'error', SQLERRM, 'state', SQLSTATE)
        );
    END;

    BEGIN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES ('ttl_cleanup_chat_completion_requests', cleanup_chat_completion_requests());
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES (
            'ttl_cleanup_chat_completion_requests',
            jsonb_build_object('timestamp', NOW(), 'error', SQLERRM, 'state', SQLSTATE)
        );
    END;

    BEGIN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES ('ttl_cleanup_model_health_history', cleanup_model_health_history());
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES (
            'ttl_cleanup_model_health_history',
            jsonb_build_object('timestamp', NOW(), 'error', SQLERRM, 'state', SQLSTATE)
        );
    END;

    BEGIN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES ('ttl_cleanup_rate_limit_alerts', cleanup_rate_limit_alerts());
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO reconciliation_logs (job_name, result)
        VALUES (
            'ttl_cleanup_rate_limit_alerts',
            jsonb_build_object('timestamp', NOW(), 'error', SQLERRM, 'state', SQLSTATE)
        );
    END;

    DELETE FROM reconciliation_logs
    WHERE (job_name LIKE 'ttl_cleanup_%' OR job_name LIKE 'ttl_rollup_%')
      AND created_at < NOW() - INTERVAL '30 days';
END;
$$;

GRANT EXECUTE ON FUNCTION rollup_chat_completion_requests() TO service_role;
GRANT EXECUTE ON FUNCTION cleanup_chat_completion_requests() TO service_role;
GRANT EXECUTE ON FUNCTION cleanup_model_health_history() TO service_role;
GRANT EXECUTE ON FUNCTION cleanup_rate_limit_alerts() TO service_role;
GRANT EXECUTE ON FUNCTION run_and_log_ttl_cleanups() TO service_role;

-- Restore mark-expired-trials to hourly. Wrapped in EXCEPTION so cron-ownership
-- mismatches (a different role created the original schedule) don't abort the
-- whole migration.
DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        BEGIN
            PERFORM cron.unschedule('mark-expired-trials')
            WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'mark-expired-trials');
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'cron.unschedule(mark-expired-trials) failed (likely ownership): %', SQLERRM;
        END;

        BEGIN
            PERFORM cron.schedule(
                'mark-expired-trials',
                '0 * * * *',  -- hourly, restoring original schedule
                $$SELECT run_and_log_expired_trials()$$
            );
            RAISE NOTICE 'Restored mark-expired-trials to hourly';
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'cron.schedule(mark-expired-trials) failed: %', SQLERRM;
        END;
    END IF;
END;
$block$;
