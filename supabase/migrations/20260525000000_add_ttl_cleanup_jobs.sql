-- TTL cleanup jobs for high-churn tables.
--
-- Context: a small number of abuse loops (anonymous traffic + unpriced free models +
-- expired-trial reuse) produced 1.95M rows in chat_completion_requests (1.7 GB) and
-- equivalent bloat in model_health_history (633 MB) and rate_limit_alerts (496 MB).
-- These tables have no natural retention pressure, so they grow unbounded.
--
-- Retention targets:
--   chat_completion_requests   30 days
--   model_health_history        7 days  (we have model_health_aggregates for long-term trends)
--   rate_limit_alerts           1 day for resolved rows; 30 days hard cap for unresolved

CREATE OR REPLACE FUNCTION cleanup_chat_completion_requests()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    deleted_count BIGINT := 0;
BEGIN
    WITH d AS (
        DELETE FROM chat_completion_requests
        WHERE created_at < NOW() - INTERVAL '30 days'
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
        WHERE created_at < NOW() - INTERVAL '30 days'
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

CREATE OR REPLACE FUNCTION run_and_log_ttl_cleanups()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO reconciliation_logs (job_name, result)
    VALUES ('ttl_cleanup_chat_completion_requests', cleanup_chat_completion_requests());

    INSERT INTO reconciliation_logs (job_name, result)
    VALUES ('ttl_cleanup_model_health_history', cleanup_model_health_history());

    INSERT INTO reconciliation_logs (job_name, result)
    VALUES ('ttl_cleanup_rate_limit_alerts', cleanup_rate_limit_alerts());

    DELETE FROM reconciliation_logs
    WHERE job_name LIKE 'ttl_cleanup_%'
      AND created_at < NOW() - INTERVAL '30 days';
END;
$$;

GRANT EXECUTE ON FUNCTION cleanup_chat_completion_requests() TO service_role;
GRANT EXECUTE ON FUNCTION cleanup_model_health_history() TO service_role;
GRANT EXECUTE ON FUNCTION cleanup_rate_limit_alerts() TO service_role;
GRANT EXECUTE ON FUNCTION run_and_log_ttl_cleanups() TO service_role;

-- Schedule daily at 04:00 UTC (off-peak). Wrapped to no-op if pg_cron is unavailable.
DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.unschedule('ttl-cleanup-daily')
        WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'ttl-cleanup-daily');

        PERFORM cron.schedule(
            'ttl-cleanup-daily',
            '0 4 * * *',
            $$SELECT run_and_log_ttl_cleanups()$$
        );
        RAISE NOTICE 'Scheduled TTL cleanup job (daily 04:00 UTC)';
    ELSE
        RAISE WARNING 'pg_cron not installed; TTL cleanup must be run manually via SELECT run_and_log_ttl_cleanups()';
    END IF;
END;
$block$;

-- Also reduce the hourly mark-expired-trials cron to daily (it logs zero work 24x/day
-- on the current dataset; see reconciliation_logs from 2026-04-14 onward).
DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.unschedule('mark-expired-trials')
        WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'mark-expired-trials');

        PERFORM cron.schedule(
            'mark-expired-trials',
            '0 3 * * *',
            $$SELECT run_and_log_expired_trials()$$
        );
        RAISE NOTICE 'Rescheduled mark-expired-trials to daily 03:00 UTC';
    END IF;
END;
$block$;
