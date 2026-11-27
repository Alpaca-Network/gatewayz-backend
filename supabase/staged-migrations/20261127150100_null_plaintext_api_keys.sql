-- STAGE 3 of 3 (HUMAN-GATED): null every remaining plaintext API key.
--
-- Do NOT move this into supabase/migrations/ until the stage-2 runbook is done:
--   1. Stage 2 is deployed (hash lookups, 20261127150000_api_key_hash_lookup.sql).
--   2. Zero `api_key_plaintext_fallback` WARNING log lines for 24-48 h.
--   3. API_KEY_PLAINTEXT_FALLBACK=false is set in every environment and has
--      soaked without auth errors.
-- Then apply this file by hand (one transaction), staging first.
--
-- Irreversible: the plaintext copies are gone afterwards. Keys stay usable
-- through key_hash, and owners can still see them via encrypted_key.

BEGIN;

-- Refuse to run if a key would become unusable or unrecoverable.
DO $$
DECLARE
    missing_hash bigint;
    orphan_legacy bigint;
BEGIN
    SELECT count(*) INTO missing_hash
    FROM public.api_keys_new
    WHERE key_hash IS NULL OR encrypted_key IS NULL;
    IF missing_hash > 0 THEN
        RAISE EXCEPTION
            '% api_keys_new rows lack key_hash/encrypted_key; rerun the backfill first',
            missing_hash;
    END IF;

    -- Legacy users.api_key values that were never copied into api_keys_new
    -- would stop authenticating. Migrate or deliberately revoke them first.
    SELECT count(*) INTO orphan_legacy
    FROM public.users u
    WHERE u.api_key IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.api_keys_new k WHERE k.api_key = u.api_key);
    IF orphan_legacy > 0 THEN
        RAISE EXCEPTION
            '% users.api_key values have no api_keys_new row; migrate or revoke them first',
            orphan_legacy;
    END IF;
END $$;

UPDATE public.api_keys_new SET api_key = NULL WHERE api_key IS NOT NULL;

UPDATE public.users SET api_key = NULL WHERE api_key IS NOT NULL;

UPDATE public.rate_limit_usage
SET api_key = NULL
WHERE api_key IS NOT NULL AND api_key NOT LIKE 'hash:%';

-- rate_limit_alerts.api_key is NOT NULL; stage 2 writes 'hash:<12 hex>'.
DO $$
BEGIN
    IF to_regclass('public.rate_limit_alerts') IS NOT NULL THEN
        UPDATE public.rate_limit_alerts
        SET api_key = 'redacted'
        WHERE api_key NOT LIKE 'hash:%';
    END IF;
END $$;

-- Matched users by plaintext; the app stopped calling it in stage 2.
DROP FUNCTION IF EXISTS public.search_user_by_api_key(TEXT);

COMMIT;
