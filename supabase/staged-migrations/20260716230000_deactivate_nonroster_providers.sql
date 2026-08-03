-- Deactivate 16 non-roster providers (MVP North Star: task 10 client purge).
--
-- These providers' client code (src/services/providers/*_client.py) has been
-- removed as part of the MVP roster shrink (docs/NORTH_STAR.md §3). The
-- gateway registry reads providers.is_active, so deactivating here removes
-- them from the live catalog, /gateways, routing, and health sweeps. Their
-- models deactivate via the existing stale-deprecation cron once the
-- provider row is inactive.
--
-- STAGING ONLY. Do NOT run against production without separate review.
--
-- Reversible: set is_active = true to restore (rows are NOT deleted so
-- historical credit_transactions / analytics FKs stay intact).

BEGIN;

-- 1. Deactivate the providers themselves.
-- huggingface intentionally NOT deactivated: catalog fetch endpoints still live pending roster decision
UPDATE "public"."providers"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "slug" IN (
    'aimo',
    'near',
    'morpheus',
    'chutes',
    'akash',
    'sybil',
    'canopywave',
    'simplismart',
    'clarifai',
    'cohere',
    'alpaca-network',
    'modelz',
    'cloudflare-workers-ai',
    'nebius',
    'alpaca'
);

-- 2. Deactivate their catalog models so they drop out of the health-gated catalog.
-- huggingface intentionally NOT deactivated: catalog fetch endpoints still live pending roster decision
UPDATE "public"."models"
SET "is_active" = false,
    "updated_at" = NOW()
WHERE "provider_id" IN (
    SELECT "id" FROM "public"."providers"
    WHERE "slug" IN (
        'aimo',
        'near',
        'morpheus',
        'chutes',
        'akash',
        'sybil',
        'canopywave',
        'simplismart',
        'clarifai',
        'cohere',
        'alpaca-network',
        'modelz',
        'cloudflare-workers-ai',
        'nebius',
        'alpaca'
    )
);

-- 3. Drop their rows from the smart-router projection if the table exists
--    (idempotent; refresh_offers_projection would otherwise re-derive them).
DO $$
BEGIN
    -- huggingface intentionally NOT deactivated: catalog fetch endpoints still live pending roster decision
    IF to_regclass('public.model_provider_offers') IS NOT NULL THEN
        DELETE FROM "public"."model_provider_offers"
        WHERE "provider_slug" IN (
            'aimo', 'near', 'morpheus', 'chutes', 'akash', 'sybil', 'canopywave',
            'simplismart', 'clarifai', 'cohere', 'alpaca-network',
            'modelz', 'cloudflare-workers-ai', 'nebius', 'alpaca'
        );
    END IF;
END $$;

COMMIT;
