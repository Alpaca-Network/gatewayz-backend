-- Auto-routing Phase 5 (gatewayz-backend#2216): per-key opt-out for the
-- model-choice auto-routing engine (Config.AUTO_ROUTING_ENABLED gates it
-- globally; this column lets an individual key opt out even when the global
-- flag is on -- it cannot turn auto-routing ON when the global flag is off).
--
-- `routing_policies` already existed (20260617000000_gatewayz_one_phase1_registry.sql)
-- for the separate provider-choice smart_router policy (cost/latency/quality/
-- balanced weights) and was never read by any code before this phase. Reusing
-- the same table -- keyed the same way by api_key_id -- rather than adding a
-- new one for a single boolean.
--
-- Guarded: skip cleanly if routing_policies is absent (mirrors the guard
-- style used throughout 20260617000000 for cross-environment safety).
DO $$
BEGIN
    IF to_regclass('public.routing_policies') IS NULL THEN
        RAISE NOTICE 'public.routing_policies not found — skipping auto_routing_enabled column';
        RETURN;
    END IF;

    ALTER TABLE public.routing_policies
        ADD COLUMN IF NOT EXISTS auto_routing_enabled boolean NOT NULL DEFAULT true;
END $$;
