-- EMERGENCY: Critical RLS gaps surfaced by Supabase advisor 2026-05-27.
--
-- Verified externally with the public anon key:
--   GET /rest/v1/users?limit=1   returned plaintext rows including api_key, email,
--                                 stripe_customer_id, stripe_subscription_id.
--   GET /rest/v1/payments        returned amounts + stripe_payment_intent_id.
--   GET /rest/v1/rate_limit_usage returned plaintext api_key (second exposure).
--   GET /rest/v1/chat_completion_requests returned per-request logs.
--   GET /rest/v1/message_feedback returned user content/PII.
--   GET /rest/v1/security_audit_log returned IPs + fingerprints.
--
-- Root cause: RLS was never enabled on these tables and the default Supabase
-- grants give anon + authenticated SELECT on everything in `public`.
--
-- The backend uses the service_role key for all writes; service_role BYPASSES RLS
-- so the application keeps working.  We:
--   1) ENABLE RLS (any non-service_role reader now gets zero rows by default)
--   2) REVOKE anon + authenticated grants (defense in depth, in case RLS is
--      ever disabled again — and to remove these tables from the publicly
--      exposed PostgREST schema entirely)
--
-- Tables in scope (this migration):
--   users, payments, rate_limit_usage, chat_completion_requests,
--   message_feedback, security_audit_log
--
-- A follow-up migration covers the remaining advisor findings (the operational
-- leakage tables: model_pricing, model_aliases, subscription_products, etc).

-- ============================================================================
-- 1) USERS — leaks api_key + email + stripe IDs
-- ============================================================================
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.users FROM anon, authenticated;

-- ============================================================================
-- 2) PAYMENTS — leaks Stripe payment_intent_id + amounts
-- ============================================================================
ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.payments FROM anon, authenticated;

-- ============================================================================
-- 3) RATE_LIMIT_USAGE — leaks plaintext api_key (second copy of the leak)
-- ============================================================================
ALTER TABLE public.rate_limit_usage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.rate_limit_usage FROM anon, authenticated;

-- ============================================================================
-- 4) CHAT_COMPLETION_REQUESTS — leaks per-request logs
-- ============================================================================
ALTER TABLE public.chat_completion_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chat_completion_requests FROM anon, authenticated;

-- ============================================================================
-- 5) MESSAGE_FEEDBACK — leaks user/message content
-- ============================================================================
ALTER TABLE public.message_feedback ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.message_feedback FROM anon, authenticated;

-- ============================================================================
-- 6) SECURITY_AUDIT_LOG — leaks IPs + fingerprints (helps attackers learn detection)
-- ============================================================================
ALTER TABLE public.security_audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.security_audit_log FROM anon, authenticated;

-- service_role retains full access (it bypasses RLS by default; explicit grants
-- here are belt-and-suspenders).
GRANT ALL ON public.users TO service_role;
GRANT ALL ON public.payments TO service_role;
GRANT ALL ON public.rate_limit_usage TO service_role;
GRANT ALL ON public.chat_completion_requests TO service_role;
GRANT ALL ON public.message_feedback TO service_role;
GRANT ALL ON public.security_audit_log TO service_role;
