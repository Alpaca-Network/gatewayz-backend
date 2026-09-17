-- Canonicalize the chat_completion_requests indexes on created_at and model_id.
--
-- WHY THIS EXISTS
--
-- 20260915183000_monitoring_chat_requests_perf.sql created idx_ccr_created_at_desc
-- on this stated premise (its own comment, lines 47-49):
--
--     "Existing indexes all lead with another column (status, model_id, user_id),
--      so none of them can drive an unfiltered ordered scan."
--
-- That was true of this repository's migration history and FALSE of the database.
-- public.chat_completion_requests already carried idx_chat_completion_requests_created_at
-- (~75 MB), which appears in NO migration in this repo -- schema drift, created
-- directly against prod at some point and never written down. So that migration
-- added a duplicate.
--
-- It is the same failure that produced seven production outages in Sep 2026: the
-- repo and the database disagreed, and we trusted the repo. The lesson, recorded
-- in the vault note: read the live schema, not the migration history, before
-- claiming an index or a column does not exist.
--
-- WHICH NAME SURVIVES, AND WHY
--
-- Keep idx_ccr_created_at_desc; drop the drift. The surviving index is the one a
-- migration describes, so after this the repo explains the database. Keeping the
-- undocumented 75 MB index instead would leave the schema holding an object no
-- one can find in version control -- which is exactly the condition that caused
-- this.
--
-- Functionally the survivor is sufficient: Postgres scans a btree in either
-- direction, so an index on (created_at DESC) serves ORDER BY created_at ASC as
-- well. Nothing is lost but bytes and write amplification.
--
-- Also dropped here: idx_chat_completion_requests_model_id (~23 MB), a strict
-- prefix of idx_ccr_model_status_created_covering created by that same migration.
-- It was flagged alongside its sibling in 20260916200000 and left for this pass.
--
-- LOCKING
--
-- Same tradeoff, and same handling, as 20260916200000 -- see that file for the
-- full reasoning. In short: DROP INDEX CONCURRENTLY cannot run inside a
-- transaction block and `supabase db push` wraps migrations in one, but DROP
-- INDEX is a catalog update plus a file unlink -- O(1) in table size, unlike
-- CREATE INDEX. lock_timeout caps the wait; a contended lock downgrades to a
-- WARNING rather than aborting the whole push (and every later migration in it)
-- over a pure optimisation.
--
-- The honest cost: a missed WARNING leaves an index in place while this
-- migration records as applied. Re-check with:
--
--   SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass))
--   FROM pg_indexes
--   WHERE tablename = 'chat_completion_requests'
--     AND indexname IN ('idx_chat_completion_requests_created_at',
--                       'idx_chat_completion_requests_model_id');
--
-- and if either survives, run out-of-band:
--
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_chat_completion_requests_created_at;
--   DROP INDEX CONCURRENTLY IF EXISTS public.idx_chat_completion_requests_model_id;

SET lock_timeout = '5s';

DO $$
DECLARE
    target TEXT;
    survivor_present BOOLEAN;
BEGIN
    -- Never drop the duplicate before confirming the survivor exists. If
    -- 20260915183000 did not apply, dropping the drift would leave the table
    -- with no created_at index at all and turn a tidy-up into an outage.
    SELECT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'chat_completion_requests'
          AND indexname = 'idx_ccr_created_at_desc'
    ) INTO survivor_present;

    IF NOT survivor_present THEN
        RAISE WARNING
            'idx_ccr_created_at_desc is absent; refusing to drop '
            'idx_chat_completion_requests_created_at, which would leave no index on '
            'created_at. Apply 20260915183000 first, then re-run this migration.';
    ELSE
        FOREACH target IN ARRAY ARRAY[
            'idx_chat_completion_requests_created_at',
            'idx_chat_completion_requests_model_id'
        ] LOOP
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'chat_completion_requests'
                  AND indexname = target
            ) THEN
                EXECUTE format('DROP INDEX IF EXISTS public.%I', target);
                RAISE NOTICE 'Dropped % (redundant)', target;
            ELSE
                RAISE NOTICE '% is already absent; nothing to do', target;
            END IF;
        END LOOP;
    END IF;
EXCEPTION
    WHEN lock_not_available THEN
        RAISE WARNING
            'Could not acquire ACCESS EXCLUSIVE on chat_completion_requests within '
            'lock_timeout; indexes left in place. Run out-of-band: '
            'DROP INDEX CONCURRENTLY IF EXISTS public.idx_chat_completion_requests_created_at; '
            'DROP INDEX CONCURRENTLY IF EXISTS public.idx_chat_completion_requests_model_id;';
END;
$$;

RESET lock_timeout;
