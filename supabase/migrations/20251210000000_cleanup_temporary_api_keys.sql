-- Migration: Cleanup Temporary API Keys from api_keys_new
-- Date: 2025-12-10
-- Purpose: Remove temporary API keys (< 40 chars) that were incorrectly migrated
--          to api_keys_new before the fix in migration 20251112000000
--
-- Background:
--   Temporary keys are short keys (30 chars) created during user registration
--   that should have been replaced with proper keys (51 chars). Some were
--   incorrectly migrated to api_keys_new. Users with these keys will get
--   new proper keys generated on their next authentication.

-- Step 1: Log the keys that will be deleted (for audit purposes)
DO $$
DECLARE
    temp_key_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO temp_key_count
    FROM api_keys_new
    WHERE LENGTH(api_key) < 40
      AND api_key LIKE 'gw_live_%';

    RAISE NOTICE 'Found % temporary API keys to clean up from api_keys_new', temp_key_count;
END $$;

-- Step 2: Delete temporary keys from api_keys_new
-- These are keys with length < 40 that follow the temporary key pattern
DELETE FROM api_keys_new
WHERE LENGTH(api_key) < 40
  AND api_key LIKE 'gw_live_%';

-- Step 3: Verify cleanup
DO $$
DECLARE
    remaining_temp_keys INTEGER;
    total_keys INTEGER;
BEGIN
    -- Check if any temporary keys remain
    SELECT COUNT(*) INTO remaining_temp_keys
    FROM api_keys_new
    WHERE LENGTH(api_key) < 40
      AND api_key LIKE 'gw_live_%';

    -- Get total key count
    SELECT COUNT(*) INTO total_keys
    FROM api_keys_new;

    IF remaining_temp_keys = 0 THEN
        RAISE NOTICE 'Cleanup complete. % keys remain in api_keys_new (all proper keys)', total_keys;
    ELSE
        RAISE WARNING 'Cleanup incomplete: % temporary keys still remain', remaining_temp_keys;
    END IF;
END $$;

-- Step 4: Update table comment to reflect cleanup
COMMENT ON TABLE public.api_keys_new IS 'API keys for user authentication with advanced security features. Legacy keys migrated on 2025-11-12. Temporary keys cleaned up on 2025-12-10.';
