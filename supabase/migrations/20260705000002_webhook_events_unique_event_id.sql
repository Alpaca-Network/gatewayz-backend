-- Migration: Enforce UNIQUE(event_id) on stripe_webhook_events
--
-- Problem:
-- The table was created two different ways across the migration history:
--   * 20251009030427_remote_schema.sql        -> PRIMARY KEY (event_id)  [unique]
--   * 20251230000001_restore_dropped_tables   -> plain column + NON-unique index
-- Depending on which path a database took, event_id may NOT be unique. Without a
-- unique constraint the insert-first webhook dedup (claim_event) cannot reject a
-- concurrent duplicate delivery, reopening the double-processing window.
--
-- Solution:
-- De-duplicate any existing rows (keep the earliest per event_id), drop the
-- non-unique index, and add a unique index. All steps are idempotent so this is
-- safe to run regardless of which historical shape the table has (a pre-existing
-- PRIMARY KEY on event_id already satisfies uniqueness; the extra unique index is
-- harmless).

-- 1. Remove duplicate rows, keeping the physically-earliest row per event_id.
DELETE FROM stripe_webhook_events a
USING stripe_webhook_events b
WHERE a.ctid > b.ctid
  AND a.event_id = b.event_id;

-- 2. Drop the non-unique index created by restore_dropped_tables (if present).
DROP INDEX IF EXISTS idx_stripe_webhook_events_event_id;

-- 3. Enforce uniqueness. IF NOT EXISTS keeps this a no-op where event_id is
--    already the primary key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_stripe_webhook_events_event_id
    ON stripe_webhook_events (event_id);

COMMENT ON INDEX uq_stripe_webhook_events_event_id IS
'Enforces one row per Stripe event id so claim_event() (insert-first webhook dedup) can reject concurrent duplicate deliveries.';
