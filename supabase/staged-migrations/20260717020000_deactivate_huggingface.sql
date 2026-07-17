-- Deactivate the huggingface provider (North Star gap-closure decision: KILL huggingface).
--
-- The huggingface catalog integration (src/services/huggingface_models.py,
-- src/services/huggingface_hub_service.py), its PROVIDER_FETCH_FUNCTIONS entry
-- in src/services/model_catalog_sync.py, its ~6 HF-backed discovery endpoints
-- in src/routes/catalog.py, its 3 admin cache/debug endpoints in
-- src/routes/admin.py, and its entry in GATEWAY_CONFIG
-- (src/services/monitoring/gateway_health_service.py) have all been removed
-- in this PR. This migration deactivates the corresponding DB rows so the
-- gateway registry (which reads providers.is_active) stops surfacing
-- huggingface in the live catalog, /gateways, routing, and health sweeps.
--
-- This was intentionally left NOT deactivated by the earlier
-- 20260716230000_deactivate_nonroster_providers.sql migration pending this
-- product decision (see docs/NORTH_STAR.md Amendments, 2026-07-17 entry).
--
-- STAGING ONLY. Do NOT run against production without separate review.
-- Do NOT move this file into supabase/migrations/ — CI auto-applies that
-- directory to prod on merge.
--
-- Reversible: set is_active = true to restore (rows are NOT deleted so
-- historical credit_transactions / analytics FKs stay intact).

BEGIN;

-- 1. Deactivate the huggingface provider row.
UPDATE "public"."providers"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "slug" = 'huggingface';

-- 2. Deactivate its catalog models so they drop out of the health-gated catalog.
UPDATE "public"."models"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "provider_id" IN (
    SELECT "id" FROM "public"."providers" WHERE "slug" = 'huggingface'
);

-- 3. Drop its rows from the smart-router projection if the table exists
--    (idempotent; refresh_offers_projection would otherwise re-derive them).
DO $$
BEGIN
    IF to_regclass('public.model_provider_offers') IS NOT NULL THEN
        DELETE FROM "public"."model_provider_offers"
        WHERE "provider_slug" = 'huggingface';
    END IF;
END $$;

COMMIT;
