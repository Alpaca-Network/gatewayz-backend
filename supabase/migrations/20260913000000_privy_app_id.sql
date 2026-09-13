-- Migration: users.privy_app_id (Privy app migration, 2026-09-13)
--
-- Gatewayz is moving its Privy project from app `cmg8fkib300g3l40dbs6autqe`
-- (the "old" app) to `cmtxc6wsn00yn0dle1k5a9bzq` (the "new" app). Privy DIDs
-- (and embedded wallets) are scoped per-app, so every one of the 17,694
-- existing users with a `privy_user_id` would otherwise get a brand-new,
-- unlinked account the next time they log in post-cutover.
--
-- `privy_app_id` records which Privy app a row's `privy_user_id` belongs to,
-- so the lazy-adoption logic in src/services/privy_migration.py can tell a
-- legacy (old-app) row from an already-migrated (new-app) one when it looks
-- up a login's verified email. See docs/PRIVY_MIGRATION.md for the full
-- design and cut-over runbook.
--
-- Idempotent: safe to run more than once (IF NOT EXISTS / WHERE-guarded
-- backfill / IF NOT EXISTS index).

-- 1. Column
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS privy_app_id TEXT NULL;

COMMENT ON COLUMN public.users.privy_app_id IS
    'The Privy app id that privy_user_id was issued under. NULL for accounts '
    'that have never linked Privy. Backfilled to the old app id for every '
    'pre-migration row with a privy_user_id; see docs/PRIVY_MIGRATION.md.';

-- 2. Backfill: every row that already has a privy_user_id got it from the
-- old app (this migration runs before the new app is ever live). Only fills
-- rows that don't already have a privy_app_id, so re-running this migration
-- (or running it after adoptions have already started stamping the new app
-- id) never clobbers a real value.
UPDATE public.users
SET privy_app_id = 'cmg8fkib300g3l40dbs6autqe'
WHERE privy_user_id IS NOT NULL
  AND privy_app_id IS NULL;

-- 3. Index: the adoption lookup filters legacy rows by
-- `lower(email) = lower(E) AND privy_app_id IN (...)`. idx_users_email_lower
-- (LOWER(email), see 20260103000000_add_admin_users_search_indexes.sql)
-- already covers the email side; this adds the privy_app_id side so that
-- combined filter doesn't fall back to a sequential scan as the table grows
-- past the current 17,694 rows.
CREATE INDEX IF NOT EXISTS idx_users_privy_app_id
    ON public.users (privy_app_id);
