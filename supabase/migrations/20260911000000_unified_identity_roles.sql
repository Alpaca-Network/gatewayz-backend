-- Migration: add 'superadmin' to the user_role enum
-- (gatewayz-backend Phase A of docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md,
-- §3 Phase A1).
-- Created: 2026-09-11
--
-- THIS FILE MUST CONTAIN ONLY THE ALTER TYPE STATEMENT. Postgres does not
-- allow a newly-added enum value to be referenced by any statement in the
-- same transaction that added it (ALTER TYPE ... ADD VALUE cannot run
-- inside a transaction block with other statements, and the new label is
-- not visible to statements in that same transaction even when it can).
-- Every downstream consumer of 'superadmin' (role_permissions rows,
-- backfills, etc.) MUST live in a later migration file so it runs in its
-- own, later transaction -- see 20260911000001_audit_log_and_staff.sql,
-- which adds the superadmin role_permissions rows.
--
-- 'user_role' was created in 20251009060000_add_user_roles.sql as
-- ENUM ('user', 'developer', 'admin').

-- Production (verified 2026-09-10): users.role is a plain varchar with no
-- enum and no CHECK constraint — the 2025 user_role enum never landed there.
-- Only touch the enum where it actually exists.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
    ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'superadmin';
  END IF;
END $$;
