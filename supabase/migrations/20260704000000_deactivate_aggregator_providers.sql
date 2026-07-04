-- Deactivate 6 aggregator/proxy providers (code removal: commit 8c730311).
--
-- These are meta-aggregators/proxies with no supply of their own; they resold
-- models already reachable directly or via OpenRouter. The gateway registry
-- reads providers.is_active, so deactivating here removes them from the live
-- catalog, /gateways, routing, and health sweeps.
--
-- Reversible: set is_active = true to restore (rows are NOT deleted so
-- historical credit_transactions / analytics FKs stay intact).

BEGIN;

-- 1. Deactivate the providers themselves.
UPDATE "public"."providers"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "slug" IN (
    'vercel-ai-gateway',
    'onerouter',
    'aihubmix',
    'anannas',
    'helicone',
    'notdiamond'
);

-- 2. Deactivate their catalog models so they drop out of the health-gated catalog.
UPDATE "public"."models"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "provider_id" IN (
    SELECT "id" FROM "public"."providers"
    WHERE "slug" IN (
        'vercel-ai-gateway',
        'onerouter',
        'aihubmix',
        'anannas',
        'helicone',
        'notdiamond'
    )
);

-- 3. Drop their rows from the smart-router projection if the table exists
--    (idempotent; refresh_offers_projection would otherwise re-derive them).
DO $$
BEGIN
    IF to_regclass('public.model_provider_offers') IS NOT NULL THEN
        DELETE FROM "public"."model_provider_offers"
        WHERE "provider_slug" IN (
            'vercel-ai-gateway', 'onerouter', 'aihubmix', 'anannas', 'helicone', 'notdiamond'
        );
    END IF;
END $$;

COMMIT;
