-- Migration: superadmin role_permissions, audit_log, admin_invites
-- (gatewayz-backend Phase A of docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md,
-- §3 Phase A1/A2/A3).
-- Created: 2026-09-11
--
-- This file runs AFTER 20260911000000_unified_identity_roles.sql, in its own
-- transaction, so the 'superadmin' enum label it adds is committed and
-- visible here -- see that file's header comment for why the label and its
-- first use cannot share a transaction.
--
-- Three independent pieces:
--   1. role_permissions rows for 'superadmin' (everything 'admin' has, plus
--      staff management and audit read), and an 'audit' read row for 'admin'.
--   2. audit_log -- append-only record of every admin/superadmin action
--      (staff changes, GPU approve/suspend, role changes, key revocations).
--      Service-role only: RLS enabled, no anon/authenticated policy, table
--      and owned sequence explicitly revoked from anon/authenticated (belt
--      and suspenders -- matches the posture usage_records/api_keys_new
--      etc. were hardened to, see tests/security/test_rls_policies_static.py).
--   3. admin_invites -- pending staff invitations (email + role + a hashed,
--      single-use, 72h token); same service-role-only posture.

-- =====================================================
-- 1. role_permissions: superadmin (mirrors admin) + audit read
-- =====================================================

INSERT INTO public.role_permissions (role, resource, action, allowed) VALUES
    ('superadmin', 'users', 'create', true),
    ('superadmin', 'users', 'read', true),
    ('superadmin', 'users', 'update', true),
    ('superadmin', 'users', 'delete', true),
    ('superadmin', 'users', 'list', true),

    ('superadmin', 'coupons', 'create', true),
    ('superadmin', 'coupons', 'read', true),
    ('superadmin', 'coupons', 'update', true),
    ('superadmin', 'coupons', 'delete', true),
    ('superadmin', 'coupons', 'list', true),
    ('superadmin', 'coupons', 'analytics', true),

    ('superadmin', 'api_keys', 'create', true),
    ('superadmin', 'api_keys', 'read', true),
    ('superadmin', 'api_keys', 'update', true),
    ('superadmin', 'api_keys', 'delete', true),
    ('superadmin', 'api_keys', 'list', true),

    ('superadmin', 'plans', 'create', true),
    ('superadmin', 'plans', 'read', true),
    ('superadmin', 'plans', 'update', true),
    ('superadmin', 'plans', 'delete', true),

    ('superadmin', 'analytics', 'read', true),
    ('superadmin', 'monitoring', 'read', true),
    ('superadmin', 'audit_logs', 'read', true),

    -- New in Phase A: staff management and the unified audit_log.
    ('superadmin', 'staff', 'manage', true),
    ('superadmin', 'audit', 'read', true),
    ('admin', 'audit', 'read', true)
ON CONFLICT (role, resource, action) DO NOTHING;

-- =====================================================
-- 2. audit_log
-- =====================================================

CREATE TABLE IF NOT EXISTS public.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    actor_user_id   BIGINT NULL REFERENCES public.users(id) ON DELETE SET NULL,
    actor_email     TEXT NULL,
    actor_auth      TEXT NOT NULL CHECK (actor_auth IN ('api_key', 'env_key', 'system')),
    action          TEXT NOT NULL,
    target_type     TEXT NULL,
    target_id       TEXT NULL,
    ip              INET NULL,
    user_agent      TEXT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON public.audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_created_at ON public.audit_log (action, created_at DESC);

COMMENT ON TABLE public.audit_log IS
    'Append-only record of admin/superadmin actions -- staff changes, GPU approve/suspend, role changes, key revocations. Written only by the backend service role via src/db/audit.py::record_audit.';

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.audit_log FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.audit_log_id_seq FROM anon, authenticated;
GRANT ALL ON public.audit_log TO service_role;
GRANT ALL ON SEQUENCE public.audit_log_id_seq TO service_role;

-- =====================================================
-- 3. admin_invites
-- =====================================================

CREATE TABLE IF NOT EXISTS public.admin_invites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL,
    role            user_role NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    invited_by      BIGINT NULL REFERENCES public.users(id) ON DELETE SET NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    accepted_at     TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_invites_email ON public.admin_invites (lower(email));

COMMENT ON TABLE public.admin_invites IS
    'Pending staff invitations -- a hashed (sha256), single-use, 72h token per invite. Raw token is emailed/returned once at creation and never stored.';

ALTER TABLE public.admin_invites ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.admin_invites FROM anon, authenticated;
GRANT ALL ON public.admin_invites TO service_role;
