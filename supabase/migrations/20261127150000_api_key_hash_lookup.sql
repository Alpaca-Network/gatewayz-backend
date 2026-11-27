-- Stage 2 of 3: stop depending on plaintext API keys in the database.
--
-- The app now looks keys up by key_hash (src/db/api_key_lookup.py) and
-- stores new keys as key_hash + encrypted_key + last4, never plaintext.
-- This migration only relaxes constraints and adds a column, so it is safe
-- on the live tables and safe to re-run:
--
--   api_keys_new.api_key   -> nullable (new keys write NULL). The existing
--                             UNIQUE (api_key) constraint allows many NULLs.
--   api_keys_new.key_hash  -> must be unique (it is the lookup key). The base
--                             schema already has api_keys_new_key_hash_key;
--                             a unique index is only created if it is missing.
--   rate_limit_usage       -> new nullable key_hash column + index. Rows
--                             written from now on carry key_hash and only a
--                             non-secret 'hash:<12 hex>' placeholder in
--                             api_key, which keeps the existing
--                             UNIQUE (api_key, window_type, window_start)
--                             working. api_key becomes nullable for stage 3.
--
-- users.api_key is already nullable (20251009030427_remote_schema.sql).
-- Plain CREATE INDEX (not CONCURRENTLY): `supabase db push` runs each file in
-- a transaction, and rate_limit_usage is small (~19k rows).
--
-- Stage 3 (nulling the plaintext columns) is NOT here: it is human-gated in
-- supabase/staged-migrations/20261127150100_null_plaintext_api_keys.sql.

ALTER TABLE public.api_keys_new ALTER COLUMN api_key DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
        WHERE n.nspname = 'public'
          AND t.relname = 'api_keys_new'
          AND i.indisunique
          AND i.indnatts = 1
          AND a.attname = 'key_hash'
    ) THEN
        CREATE UNIQUE INDEX api_keys_new_key_hash_unique_idx
            ON public.api_keys_new (key_hash);
    END IF;
END $$;

ALTER TABLE public.rate_limit_usage ADD COLUMN IF NOT EXISTS key_hash text;

ALTER TABLE public.rate_limit_usage ALTER COLUMN api_key DROP NOT NULL;

CREATE INDEX IF NOT EXISTS rate_limit_usage_key_hash_window_idx
    ON public.rate_limit_usage (key_hash, window_type, window_start);

COMMENT ON COLUMN public.rate_limit_usage.key_hash IS
    'sha256(KEY_HASH_SALT || api key), same value as api_keys_new.key_hash. '
    'Lookup column since stage 2; api_key holds only a hash: placeholder.';
