-- Full security hardening based on Supabase advisor findings 2026-05-27.
--
-- Companion to 20260527000000_emergency_rls_lockdown.sql which already locked
-- down the most critical leak (users, payments, rate_limit_usage,
-- chat_completion_requests, message_feedback, security_audit_log).
--
-- This migration addresses:
--   A) The remaining 11 RLS-disabled tables (operational + competitive data)
--   B) 8 SECURITY DEFINER views that bypass underlying-table RLS
--   C) 58 functions missing SET search_path (search_path injection risk)
--
-- All changes are safe for the FastAPI backend because it uses the
-- service_role key which BYPASSES RLS. Frontend code that talks directly to
-- PostgREST with the anon key will need to be reviewed (currently nothing
-- shows it does — only the backend hits the DB).

-- ============================================================================
-- A) Lock down remaining tables (RLS + revoke anon/authenticated grants)
-- ============================================================================
DO $block$
DECLARE
    t text;
    tables text[] := ARRAY[
        'rate_limit_usage',                  -- already locked in prior migration but idempotent
        'model_routing_rules',               -- reveals routing logic
        'model_quality_scores',              -- internal quality data
        'reconciliation_logs',               -- internal job results
        'temporary_email_domains',           -- bot-detection list
        'unique_models',                     -- catalog (less sensitive but private)
        'unique_models_provider',
        'chat_completion_daily_aggregates',  -- usage aggregates per user
        'system_config',                     -- internal config
        'model_pricing',                     -- pricing strategy
        'subscription_products',             -- stripe product IDs + pricing tiers
        'model_aliases',                     -- routing logic
        'model_provider_mappings',           -- routing logic
        'partner_trials',                    -- partner deal terms
        'partner_trial_analytics'            -- partner usage metrics
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
        EXECUTE format('GRANT ALL ON public.%I TO service_role', t);
    END LOOP;
END;
$block$;

-- ============================================================================
-- B) Rebuild SECURITY DEFINER views without the SECURITY DEFINER property.
--    The views remain readable by the backend via service_role; we just stop
--    them from bypassing RLS on the underlying tables.
--
--    pg_views.definition gives us the SELECT body — we wrap it in a fresh
--    CREATE OR REPLACE VIEW without security_invoker setting → defaults to
--    SECURITY INVOKER (the safe default).
-- ============================================================================
DO $block$
DECLARE
    v text;
    views text[] := ARRAY[
        'butter_cache_analytics',
        'unique_models_summary',
        'model_usage_analytics',
        'api_key_tracking_quality',
        'ongoing_downtime_incidents',
        'provider_health_current',
        'model_status_current',
        'unique_models_provider_count'
    ];
    view_def text;
BEGIN
    FOREACH v IN ARRAY views LOOP
        SELECT pg_get_viewdef(format('public.%I', v)::regclass, true)
          INTO view_def;
        IF view_def IS NULL THEN
            RAISE WARNING 'View public.% not found, skipping', v;
            CONTINUE;
        END IF;
        -- Recreate as SECURITY INVOKER (default). Postgres 15+ supports
        -- `security_invoker = true` explicitly but plain CREATE OR REPLACE
        -- VIEW already strips SECURITY DEFINER unless explicitly set.
        EXECUTE format(
            'CREATE OR REPLACE VIEW public.%I WITH (security_invoker = true) AS %s',
            v, view_def
        );
        -- Keep service_role grant; remove anon/authenticated grants.
        EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', v);
        EXECUTE format('GRANT SELECT ON public.%I TO service_role', v);
    END LOOP;
END;
$block$;

-- ============================================================================
-- C) Set search_path on every function in `public` that lacks one. Iterates
--    pg_proc dynamically so all 58 functions are covered (and any new ones).
--    SET search_path = pg_catalog, public is the recommended hardening.
-- ============================================================================
DO $block$
DECLARE
    r record;
    sig text;
BEGIN
    FOR r IN
        SELECT n.nspname AS schema_name,
               p.proname AS func_name,
               pg_get_function_identity_arguments(p.oid) AS args,
               p.proconfig
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.prokind = 'f'
           AND (p.proconfig IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM unnest(p.proconfig) cfg
                     WHERE cfg LIKE 'search_path=%'
                ))
    LOOP
        sig := format('%I.%I(%s)', r.schema_name, r.func_name, r.args);
        BEGIN
            EXECUTE format(
                'ALTER FUNCTION %s SET search_path = pg_catalog, public',
                sig
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Could not set search_path on %: %', sig, SQLERRM;
        END;
    END LOOP;
END;
$block$;
