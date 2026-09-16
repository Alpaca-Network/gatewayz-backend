-- Drop idx_chat_completion_requests_model_id_status: strictly redundant.
--
-- Why
-- ---
-- 20260915183000_monitoring_chat_requests_perf.sql added
--
--   idx_ccr_model_status_created_covering (model_id, status, created_at DESC)
--       INCLUDE (input_tokens, output_tokens, processing_time_ms)
--
-- which makes idx_chat_completion_requests_model_id_status (model_id, status) a
-- strict leading prefix of it. Every predicate the old index can serve, the new
-- one serves at least as well, and it answers the aggregate paths as index-only
-- scans on top. Keeping both buys nothing and costs a second index maintenance
-- write on the hottest insert path in the schema -- one row per chat completion.
-- That follow-up was named in the previous migration's comments and deliberately
-- deferred so that migration could stay additive; this is that follow-up.
--
-- Verified against production (project ynleroehyrmaafkgjgmr) on 2026-09-16,
-- before writing this file, via pg_indexes / pg_stat_user_indexes:
--
--   idx_chat_completion_requests_model_id_status   present, 23 MB,    826 scans
--   idx_ccr_model_status_created_covering          present, 4712 kB,  145 scans
--   public.chat_completion_requests                83,173 live rows
--
-- (Scan counters are cumulative since stats_reset 2025-09-26, so 826 is roughly
-- two lookups a day over a year. The covering index has only been in place since
-- 2026-09-15, which is why its counter is lower.)
--
-- Locking: why not CONCURRENTLY
-- -----------------------------
-- DROP INDEX CONCURRENTLY cannot run inside a transaction block, and
-- `supabase db push` (.github/workflows/supabase-migrations.yml) wraps the push
-- in one. That is why no migration in this repo uses CONCURRENTLY, and shipping
-- it here would simply fail the migration job on merge.
--
-- A plain DROP INDEX is acceptable here, and this is not the same tradeoff as a
-- plain CREATE INDEX. Dropping is a catalog update plus a file unlink: the work
-- is O(1) in table size, not proportional to it. The only real cost is the
-- ACCESS EXCLUSIVE lock on chat_completion_requests, which must wait behind
-- in-flight statements and blocks new ones while it waits. On this table the
-- statements are single-row inserts, and the migration immediately before this
-- one already took a *heavier* lock profile on the same table -- a SHARE lock
-- held for an entire index build -- so this is strictly less disruptive than
-- what has already been accepted here.
--
-- The lock wait is bounded rather than left open-ended:
--
--   * lock_timeout caps the wait at 5s, so a long-running reader cannot cause a
--     pile-up of blocked inserts behind our lock request.
--   * If the lock is not granted in time the DROP is skipped with a WARNING
--     instead of aborting the transaction. Failing hard would abort the whole
--     `supabase db push` -- including any later migration in the same push --
--     over what is purely an optimisation, and would leave main red on merge.
--     Skipping leaves the status quo (a redundant index) intact, which is the
--     situation this migration exists to improve, not a regression.
--
-- The honest downside of that choice: if the WARNING is missed in the job log,
-- the drop silently does not happen and the migration is still recorded as
-- applied, so it will not retry on its own. To settle it, re-check:
--
--   SELECT indexname FROM pg_indexes
--    WHERE schemaname = 'public'
--      AND tablename  = 'chat_completion_requests'
--      AND indexname  = 'idx_chat_completion_requests_model_id_status';
--
-- and if the row is still there, run the out-of-band statement, which takes no
-- blocking lock at all:
--
--   DROP INDEX CONCURRENTLY IF EXISTS
--       public.idx_chat_completion_requests_model_id_status;
--
-- Idempotent: IF EXISTS, so re-applying (or applying after the manual drop) is a
-- no-op.
--
-- Not dropped here, recorded for whoever picks this up next
-- --------------------------------------------------------
-- The same production index listing turned up two more redundancies on this
-- table. Both are out of scope for this change and neither has been touched:
--
--   * idx_chat_completion_requests_model_id (model_id), 23 MB -- also a strict
--     prefix of idx_ccr_model_status_created_covering.
--   * idx_chat_completion_requests_created_at (created_at DESC), 75 MB -- an
--     exact duplicate of idx_ccr_created_at_desc, which 20260915183000 created
--     on the stated premise that no existing index led with created_at. That
--     premise was wrong. Note this one appears in no migration in this repo, so
--     it is schema drift: dropping it needs a decision about which of the two
--     names is canonical, not just a DROP.

SET lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'idx_chat_completion_requests_model_id_status'
          AND c.relkind = 'i'
    ) THEN
        RAISE NOTICE 'idx_chat_completion_requests_model_id_status is already absent; nothing to do';
        RETURN;
    END IF;

    EXECUTE 'DROP INDEX IF EXISTS public.idx_chat_completion_requests_model_id_status';
    RAISE NOTICE 'Dropped idx_chat_completion_requests_model_id_status (redundant with idx_ccr_model_status_created_covering)';
EXCEPTION
    WHEN lock_not_available THEN
        RAISE WARNING
            'Could not acquire ACCESS EXCLUSIVE on chat_completion_requests within lock_timeout; '
            'idx_chat_completion_requests_model_id_status was NOT dropped. '
            'Run out-of-band: DROP INDEX CONCURRENTLY IF EXISTS public.idx_chat_completion_requests_model_id_status;';
END;
$$;

RESET lock_timeout;
