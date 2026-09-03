-- Migration: usage_records hardening + drop leftover permissive policy
-- Created: 2026-09-03
-- Description:
--   Milestone 3 (gatewayz-backend#2255-#2260), threat model
--   docs/security/ANONYMITY_THREAT_MODEL.md §5 L9/L10.
--
--   L9: usage_records has a plaintext `api_key` column, was never touched by
--   the 2026-05-27 emergency RLS lockdown (20260527000000/000001/000002), so
--   it still has RLS disabled and the default Supabase grants that leaked
--   `users`/`payments`/`rate_limit_usage` in that incident. This migration:
--     1) adds `api_key_id` (FK to api_keys_new) + `api_key_last4` so the app
--        can stop writing the plaintext key (see src/db/users.py record_usage,
--        changed in the same PR). The historical plaintext `api_key` column
--        is intentionally NOT touched here -- nulling + dropping it is staged
--        in supabase/staged-migrations/20260903100000_drop_usage_records_api_key.sql
--        and must run only after the app-side change below has soaked in prod.
--     2) enables RLS, revokes anon/authenticated grants, and adds an explicit
--        deny-all policy -- not just an absence-of-policy default-deny --
--        so the table is not one accidental GRANT away from repeating the
--        2026-05-27 incident.
--
--   L10: the chat_completion_requests stub migration
--   (20251226000000_create_chat_completion_requests_stub.sql) created a
--   `USING (true)` SELECT policy for anon+authenticated. The 2026-05-27
--   migrations REVOKEd table-level grants on this table (making the policy
--   inert) but never dropped the policy row itself, unlike the 8 other
--   always-true policies cleaned up in 20260527000002. Drop it here so a
--   future re-GRANT doesn't silently make the table world-readable again.
--   The exact same policy was re-created by 20251227000000 (drop-then-
--   recreate around a user_id column type fix) and never dropped again --
--   that live instance is the one this migration removes.
--
--   L9-adjacent gap found while writing the static policy-footgun test
--   (tests/security/test_rls_policies_static.py) required by this same
--   ticket: `activity_log`, `api_keys_new`, and `credit_transactions` still
--   carry the original `GRANT ALL ... TO anon/authenticated` from the base
--   schema dump (20251009030427_remote_schema.sql). The 2026-05-27 migrations
--   dropped their always-true policies (20260527000002) but, unlike
--   `users`/`payments`/`chat_completion_requests` (20260527000000) or the 15
--   tables in 20260527000001's REVOKE loop, never revoked the grant itself.
--   Section 4 below closes that -- same REVOKE-only pattern as those two
--   migrations (no new deny policy: these tables' remaining policies are
--   real per-row predicates, not always-true, so REVOKE alone restores the
--   intended default-deny posture without touching them).

-- ============================================================================
-- 1) usage_records: add api_key_id / api_key_last4 (writer stops persisting
--    the plaintext key as of this same PR)
-- ============================================================================
ALTER TABLE public.usage_records
    ADD COLUMN IF NOT EXISTS api_key_id bigint REFERENCES public.api_keys_new(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS api_key_last4 text;

CREATE INDEX IF NOT EXISTS idx_usage_records_api_key_id ON public.usage_records (api_key_id);

-- ============================================================================
-- 2) usage_records: lock grants + RLS, same pattern as the 2026-05-27
--    emergency lockdown (20260527000000_emergency_rls_lockdown.sql).
-- ============================================================================
ALTER TABLE public.usage_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.usage_records FROM anon, authenticated;
GRANT ALL ON public.usage_records TO service_role;

DROP POLICY IF EXISTS usage_records_service_only ON public.usage_records;
CREATE POLICY usage_records_service_only
    ON public.usage_records
    FOR ALL
    TO anon, authenticated
    USING (false)
    WITH CHECK (false);

-- ============================================================================
-- 3) chat_completion_requests: drop the leftover USING (true) stub policy.
--    Exact name from 20251226000000_create_chat_completion_requests_stub.sql.
--    REVOKE already precedes this (20260527000000), so the policy is inert
--    today -- this just removes the footgun.
-- ============================================================================
DROP POLICY IF EXISTS "Allow users to read their own chat completion requests"
    ON public.chat_completion_requests;

-- ============================================================================
-- 4) activity_log / api_keys_new / credit_transactions: close the leftover
--    anon/authenticated GRANT ALL from the base schema dump. RLS is already
--    enabled on all three and their always-true policies are already gone
--    (20260527000002); this REVOKE is the missing piece for the same
--    default-deny posture the 2026-05-27 migrations gave every other table.
-- ============================================================================
REVOKE ALL ON public.activity_log FROM anon, authenticated;
REVOKE ALL ON public.api_keys_new FROM anon, authenticated;
REVOKE ALL ON public.credit_transactions FROM anon, authenticated;

GRANT ALL ON public.activity_log TO service_role;
GRANT ALL ON public.api_keys_new TO service_role;
GRANT ALL ON public.credit_transactions TO service_role;
