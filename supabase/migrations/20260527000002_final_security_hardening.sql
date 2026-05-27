-- Final security hardening pass after migrations 20260527000000 + 000001.
--
-- Remaining Supabase advisor findings:
--   A) 18 SECURITY DEFINER functions are EXECUTE-grantable to anon and
--      authenticated. SECURITY DEFINER functions run with the OWNER's
--      privileges so they bypass RLS on whatever they touch. Anyone with the
--      publishable anon key can call them via /rest/v1/rpc/<func>.
--   B) 9 tables have RLS policies with `USING (true)` (rls_policy_always_true).
--      These were redundant once we REVOKEd the table-level grants in
--      migration 000000 (revoke fires before RLS), but they're still a
--      footgun — if someone later re-grants SELECT on the table, the
--      always-true policy makes everything readable again.
--   C) provider_stats_24h matview is exposed via PostgREST API.
--
-- All changes are safe for the FastAPI backend (uses service_role; bypasses
-- both EXECUTE grants and RLS policies).

-- ============================================================================
-- A) Revoke EXECUTE on internal SECURITY DEFINER functions from anon + authenticated
-- ============================================================================
DO $block$
DECLARE
    f text;
    funcs text[] := ARRAY[
        'cleanup_chat_completion_requests',
        'cleanup_model_health_history',
        'cleanup_rate_limit_alerts',
        'get_admin_notification_unread_count',
        'get_api_key_request_summary',
        'get_available_coupons',
        'get_chat_completion_summary_by_api_key',
        'get_chat_completion_summary_by_filters',
        'is_coupon_redeemable',
        'mark_all_admin_notifications_read',
        'mark_expired_trials',
        'reconcile_paid_users_trial_status',
        'record_trial_grant',
        'rollup_chat_completion_requests',
        'run_and_log_expired_trials',
        'run_and_log_trial_reconciliation',
        'run_and_log_ttl_cleanups',
        'update_model_tier'
    ];
    proc_oid oid;
BEGIN
    FOREACH f IN ARRAY funcs LOOP
        FOR proc_oid IN
            SELECT p.oid FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = f
        LOOP
            EXECUTE format(
                'REVOKE EXECUTE ON FUNCTION %s FROM anon, authenticated, PUBLIC',
                proc_oid::regprocedure
            );
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION %s TO service_role',
                proc_oid::regprocedure
            );
        END LOOP;
    END LOOP;
END;
$block$;

-- ============================================================================
-- B) Drop always-true RLS policies. The tables are already protected by
--    REVOKEd table grants (migration 000000) and being absent from
--    anon/authenticated. Dropping these policies removes a footgun: if
--    SELECT is ever re-granted, the always-true policy would make the
--    table publicly readable again.
-- ============================================================================
DO $block$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT schemaname, tablename, policyname
          FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename IN (
               'activity_log',
               'api_keys_new',
               'coupon_redemptions',
               'coupons',
               'credit_transactions',
               'payments',
               'users',
               'velocity_mode_events'
           )
           AND (
               qual = 'true' OR qual IS NULL  -- USING (true) or no USING clause
               OR with_check = 'true'
           )
    LOOP
        BEGIN
            EXECUTE format(
                'DROP POLICY IF EXISTS %I ON public.%I',
                r.policyname, r.tablename
            );
            RAISE NOTICE 'Dropped always-true policy %.%', r.tablename, r.policyname;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Could not drop policy %.%: %', r.tablename, r.policyname, SQLERRM;
        END;
    END LOOP;
END;
$block$;

-- ============================================================================
-- C) Remove materialized view from PostgREST API
-- ============================================================================
DO $block$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_matviews
         WHERE schemaname = 'public' AND matviewname = 'provider_stats_24h'
    ) THEN
        EXECUTE 'REVOKE ALL ON public.provider_stats_24h FROM anon, authenticated';
        EXECUTE 'GRANT SELECT ON public.provider_stats_24h TO service_role';
    END IF;
END;
$block$;
