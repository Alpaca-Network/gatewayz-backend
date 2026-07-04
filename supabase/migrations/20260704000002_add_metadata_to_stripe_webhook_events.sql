-- Add the missing `metadata` column to stripe_webhook_events.
--
-- The table is created in 20251109000000_add_webhook_event_tracking.sql WITH a
-- `metadata JSONB` column, but a later re-creation
-- (20251230000001_restore_dropped_tables.sql) uses CREATE TABLE IF NOT EXISTS
-- WITHOUT the column, so on databases where the table was (re)built from that
-- definition the column is absent. record_processed_event() writes `metadata`,
-- which then fails with PGRST204 ("Could not find the 'metadata' column of
-- 'stripe_webhook_events' in the schema cache"), breaking webhook event dedup
-- (idempotency layer 1). This restores the column idempotently.

ALTER TABLE public.stripe_webhook_events
    ADD COLUMN IF NOT EXISTS metadata JSONB;

COMMENT ON COLUMN public.stripe_webhook_events.metadata IS 'Additional event metadata for debugging';

-- Refresh the PostgREST schema cache so the API sees the column immediately.
NOTIFY pgrst, 'reload schema';
