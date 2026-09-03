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

`tests/security/test_rls_anon_lockdown.py` verifies the revoked/denied tables
live, against the real anon key, when `SUPABASE_URL`/`SUPABASE_ANON_KEY` are
set. `tests/security/test_rls_policies_static.py` verifies the migration
*history* never leaves an always-true policy without a later drop, or a
target table without a revoke, for every table in this document — it runs
in every test suite, no live credentials needed.

## What's still open

- Frontend code has never been observed talking to PostgREST with the anon
  key directly (comment carried over from `20260527000001`) — if that
  changes, every table above needs a real per-row policy for the roles it
  actually uses, not just a revoke.
- `usage_records.api_key` (plaintext) is NULL for all new rows as of this
  migration but the column and historical values still exist until the
  staged drop migration is run manually.
