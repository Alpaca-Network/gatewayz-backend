# Data Access — Who Can Read What

**Status:** current as of the 20260903100000 migration (gatewayz-backend#2258).
Related: [Security Incident Response — Exposed API Keys](./SECURITY_INCIDENT_RESPONSE.md).

## Background — the 2026-05-27 incident

On 2026-05-27 the Supabase advisor flagged that several `public` tables had
Row Level Security (RLS) disabled or had an always-true (`USING (true)`)
policy, combined with Supabase's default grants (`anon`/`authenticated` get
`ALL` on every table unless revoked). Anyone with the public **anon** key —
which ships in any client that talks to Supabase directly — could read
`users` (plaintext `api_key`, email, Stripe IDs), `payments`, and other
tables outright. `20260527000000_emergency_rls_lockdown.sql`,
`20260527000001_full_security_hardening.sql`, and
`20260527000002_final_security_hardening.sql` fixed the tables the advisor
found. This backend's rule since then: **the FastAPI app talks to Supabase
exclusively with the `service_role` key**, which bypasses RLS by design —
`anon`/`authenticated` are not expected to touch the database directly, so
every application table should end up with RLS enabled, no permissive
policy for those two roles, and their grants revoked.

## 2026-09-03 follow-up (this migration)

Two gaps this ticket (#2258, threat model L9/L10) closed that the 2026-05-27
migrations missed:

- **`usage_records`** (L9) was never touched by any of the three May
  migrations — it still had RLS disabled and the original
  `GRANT ALL ... TO anon, authenticated` from the base schema dump, and its
  `api_key` column stored the plaintext key. Fixed by
  `20260903100000_usage_records_hardening.sql`: RLS enabled, grants revoked,
  an explicit `USING (false) WITH CHECK (false)` deny policy added (not just
  RLS-enabled-with-no-policy — a future accidental re-GRANT should still hit
  a hard deny), and the writer (`src/db/users.py record_usage`) switched
  from writing the plaintext key to `api_key_id` (FK to `api_keys_new`) +
  `api_key_last4`. The historical plaintext column is dropped separately, in
  a human-gated staged migration
  (`supabase/staged-migrations/20260903100000_drop_usage_records_api_key.sql`),
  once the writer change has soaked in production.
- **`chat_completion_requests`** (L10) had a leftover
  `USING (true)` policy for `anon, authenticated` — inert today because the
  2026-05-27 lockdown revoked the table's grants, but still a footgun: a
  future re-GRANT would make the whole table world-readable again. Dropped
  in the same migration.
- **`activity_log`, `api_keys_new`, `credit_transactions`** — found while
  building the static footgun test this ticket also adds
  (`tests/security/test_rls_policies_static.py`): all three still carried
  the original `GRANT ALL ... TO anon, authenticated` from the base schema
  dump. Their always-true policies were already dropped in
  `20260527000002`, but the underlying grant was never revoked, unlike
  every other table the May migrations touched. Closed with the same
  REVOKE used elsewhere in this migration.
- **Owned sequences** (fix round 1, PR review) — table-level `REVOKE`
  doesn't touch grants on a table's owned identity/serial sequence.
  `usage_records_id_seq`, `activity_log_id_seq`, `api_keys_new_id_seq`,
  `credit_transactions_id_seq`, and — caught by extending the static test to
  check every target table, not just the four above — `users_id_seq` and
  `payments_id_seq` (whose *tables* were locked down in `20260527000000` but
  whose sequences never were) all still had `GRANT ALL` to
  `anon, authenticated` from the base schema dump. Closed in the same
  migration with a dynamic `REVOKE ALL ON SEQUENCE` loop.

## Current access matrix (application tables)

| Table | `anon` / `authenticated` | `service_role` (the app) |
|---|---|---|
| `users`, `payments`, `chat_completion_requests`, `rate_limit_usage`, `message_feedback`, `security_audit_log` | RLS enabled, table grants revoked (20260527000000); `users`/`payments` sequence grants revoked (20260903100000) | full access |
| `activity_log`, `api_keys_new`, `credit_transactions`, `coupon_redemptions`, `coupons`, `velocity_mode_events` | RLS enabled, always-true policies dropped (20260527000002); table + sequence grants revoked for `activity_log`/`api_keys_new`/`credit_transactions` (20260903100000) | full access |
| `usage_records` | RLS enabled, table + sequence grants revoked, explicit deny policy (20260903100000) | full access |
| 15 operational tables (`model_pricing`, `system_config`, `subscription_products`, …) | RLS enabled, grants revoked (20260527000001) | full access |
| `user_wallets`, `wallet_stakes`, `faucet_claims` | RLS enabled, never granted a policy (default-deny by omission) | full access |
| `audit_log`, `admin_invites` | RLS enabled, table grants revoked (`audit_log`'s sequence too) (20260911000001) | full access |

`tests/security/test_rls_anon_lockdown.py` verifies the revoked/denied tables
live, against the real anon key, when `SUPABASE_URL`/`SUPABASE_ANON_KEY` are
set. `tests/security/test_rls_policies_static.py` verifies the migration
*history* never leaves an always-true policy without a later drop, or a
target table without a revoke, for every table in this document — it runs
in every test suite, no live credentials needed.

## Admin WAYZ ops status endpoint

`GET /admin/wayz/status` (`src/routes/admin_wayz.py`, `Depends(require_admin_or_env_key)`)
aggregates counts and summaries across `wallet_stakes`, `faucet_claims`,
`user_wallets`, `gpu_providers`, `gpu_nodes`, `provider_work`,
`provider_earnings`, and `provider_settlements` — all read via the
`service_role` client (`src/db/*`), same as every other admin route, never
via a client-facing anon/authenticated PostgREST call. It is not a new RLS
exposure: no table above gained a policy because of this endpoint.

What it exposes to admins only (never to `anon`/`authenticated`, and never
returned by any non-admin route): pending GPU operators' `payout_wallet_address`
and `user_id` (`pending_approvals`), the most recent settlement's
`provider_id`/`tx_hash`/`amount_wei` (`gpu.last_settlement`), and aggregate
wei-scale totals for staking/earnings. It does NOT expose any user's
`wallet_address`, individual `faucet_claims`/`provider_work` rows, or
anything from `users`/`payments`/`chat_completion_requests` — the response
is aggregate counts plus provider (not end-user) identifying fields only.

## Admin unified status endpoint

`GET /admin/status` (`src/routes/admin_status.py`, same
`Depends(require_admin_or_env_key)` as `GET /admin/wayz/status`)
generalizes the WAYZ ops page into jobs + external integration health +
secret presence. It reads no table this document doesn't already cover —
`wayz` reuses `admin_wayz.py`'s own block builders (`src/db/*`, same
`service_role` client), and `integrations` (`src/services/integrations_health.py`)
performs its own external health checks (Resend, the Fuji RPC, a
`select id limit 1` against `users`, Redis `PING`) rather than reading a
table directly.

What it exposes to admins only: the **presence and rotation age** of a fixed
allow-list of secret env var names (`ADMIN_API_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_KEY`, `RESEND_API_KEY`,
`PRIVY_APP_ID`, `PRIVY_VERIFICATION_KEY`, `WAYZ_FAUCET_MINTER_PRIVATE_KEY`,
`WAYZ_REWARDS_POOL_PRIVATE_KEY`, `STRIPE_SECRET_KEY`, `SENTRY_DSN`,
`GATEWAYZ_AUTH_BRIDGE_SECRET`, `UPSTREAM_ABUSE_PSEUDONYM` --
`src/services/secrets_registry.py::SECRET_NAMES`) as
`{present, source: "env", first_seen_at, age_days, rotate_due,
fingerprint_known}`, plus the health status of each external integration.
`first_seen_at`/`age_days`/`rotate_due` come from a one-way sha256
fingerprint of the value (`secrets_registry.fingerprint`, salted by
`SECRET_FP_SALT`, truncated to 12 hex chars) recorded at startup
(`record_secret_fingerprints()`) purely to detect when a value changes --
the fingerprint itself is never returned by this endpoint or logged, only
used internally to compare "did this rotate". `rotate_due` fires once
`age_days >= SECRET_ROTATION_DAYS` (env, default 90). It never returns a
secret's value, length, hash, or fingerprint, and never exposes anything
from `users`/`payments`/`chat_completion_requests`.
`tests/routes/test_admin_status.py` asserts no configured secret's value
*or its fingerprint* appears anywhere in the response body;
`tests/services/test_secrets_registry.py` covers the fingerprint/age logic
itself.

## `gpu_providers.approved_by` is NULL for ADMIN_API_KEY-authenticated approvals

`POST /gpu/admin/providers/{id}/approve|suspend` accept
`require_admin_or_env_key` (see "Admin WAYZ ops status endpoint" above) --
either a real admin user's API key, or the `ADMIN_API_KEY` env var (the
admin panel's server-side proxy uses the latter, never a specific user's
key). `gpu_providers.approved_by` is an int FK to `users(id)`; the env-key
auth path has no user id to attribute the approval to, so `approved_by`
is written as `NULL` for those calls -- it does not mean "unapproved" or
"system default," it means "approved via the admin panel's shared
`ADMIN_API_KEY`, not a traceable individual account." `src/routes/gpu.py`
logs an explicit line (`"gpu provider %s approved/suspended via
ADMIN_API_KEY (no user id)"`) on this path so the acting admin is still
recoverable from request/access logs around that timestamp, even though
the DB row itself can't name them.

## Unified admin identity: staff API and audit_log (Phase A)

`docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md` §3
Phase A adds `role='superadmin'` and two service-role-only tables:

- `audit_log` -- append-only record of admin/superadmin actions (staff
  changes, GPU approve/suspend, role changes, key revocations), written
  only by `src/db/audit.py::record_audit`. Readable via `GET /admin/audit`
  by `admin` or `superadmin`; never exposed to the user it's about.
- `admin_invites` -- pending staff invitations. Stores a sha256 hash of a
  32-byte random token (`token_hash`, `UNIQUE`), never the raw token. The
  raw token is returned exactly once, in the `POST /admin/staff/invite`
  response (or emailed), and is the sole credential needed to claim the
  invite via `POST /auth/accept-invite` -- claiming additionally requires
  the accepting user's verified account email to match `admin_invites.email`.

`src/routes/admin_staff.py` (all `require_superadmin` except
`GET /admin/staff`, which is `require_admin`) is the only writer of
`users.role` outside `src/routes/roles.py`'s legacy `/admin/roles/update`.
Both paths invalidate the in-memory user cache
(`src/db/users.py::invalidate_user_cache_by_id`) on every role change --
without that, a just-demoted admin's stale cached user object would keep
passing `require_admin` for up to the cache's 60s TTL.

## What's still open

- Frontend code has never been observed talking to PostgREST with the anon
  key directly (comment carried over from `20260527000001`) — if that
  changes, every table above needs a real per-row policy for the roles it
  actually uses, not just a revoke.
- `usage_records.api_key` (plaintext) was dropped entirely by
  `supabase/migrations/20260903100000_drop_usage_records_api_key.sql`
  (applied by hand 2026-09-10, promoted from `staged-migrations/` to
  `migrations/` in Phase D, D3 -- see `docs/DATABASE_MIGRATIONS.md`).
