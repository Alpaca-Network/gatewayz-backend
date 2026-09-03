-- STAGED -- human-gated. Do NOT apply until the app change in the same PR as
-- supabase/migrations/20260903100000_usage_records_hardening.sql (record_usage
-- writing api_key_id/api_key_last4 instead of the plaintext api_key column,
-- gatewayz-backend#2258, threat model L9) has been deployed to production and
-- soaked. Once record_usage has stopped writing plaintext keys, nulling and
-- dropping the column is safe -- see supabase/staged-migrations/README.md.

UPDATE public.usage_records SET api_key = NULL WHERE api_key IS NOT NULL;
ALTER TABLE public.usage_records DROP COLUMN api_key;
