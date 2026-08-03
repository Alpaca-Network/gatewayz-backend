-- Drop the user_memory table (North Star gap-closure decision: CUT user_memory).
--
-- src/routes/user_memory.py, src/db/user_memory.py, and the now-dead
-- MEMORY_CAPTURE_ENABLED / CONTEXT_ASSEMBLY_ENABLED (+ related budget) config
-- flags have all been removed in this PR. The Phase 4 "context assembly"
-- consumer that was meant to read this table
-- (src/services/context_assembly.py, referenced in comments/migration 5 of
-- 20260617000000_gatewayz_one_phase1_registry.sql) was never built, so this
-- table has had no reader/writer other than the now-deleted user_memory
-- routes.
--
-- NOTE: The frontend currently calls the /v1/user/memory endpoints that
-- backed this table. A parallel frontend PR removes those calls; until it
-- ships, frontend requests to that path will 404. This is expected per the
-- North Star gap-closure decision, not a regression to fix here.
--
-- STAGING ONLY. Do NOT run against production without separate review.
-- Do NOT move this file into supabase/migrations/ — CI auto-applies that
-- directory to prod on merge.
--
-- NOT reversible once run (DROP, not deactivate) — this table held no
-- billing-relevant or FK-referenced data (see CREATE TABLE IF NOT EXISTS
-- public.user_memory in 20260617000000_gatewayz_one_phase1_registry.sql;
-- no other table references it by FK).

BEGIN;

DROP TABLE IF EXISTS public.user_memory CASCADE;

COMMIT;
