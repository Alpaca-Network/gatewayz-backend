# Privy App Migration (2026-09-13)

Gatewayz is moving its Privy project from the old app
(`cmg8fkib300g3l40dbs6autqe`) to a new one (`cmtxc6wsn00yn0dle1k5a9bzq`).
This document is the design and the cut-over runbook for that move.

## Why this needs a design at all

Privy DIDs (`did:privy:…`) and embedded wallets are scoped **per app**. The
same person logging into the new app gets a brand-new DID and a brand-new
embedded wallet address — Privy has no concept of "the same user, different
app". Backend `/auth` (`src/routes/auth.py`) keys existing-account lookup
on `privy_user_id` alone once a token is verified (see the "M2" security
comments around `_handle_existing_user` and its callers) — this is
deliberate and load-bearing: it's what closes the account-takeover hole
where a client-supplied email could be used to claim someone else's row. A
naive app-id flip would, correctly by that same rule, treat every returning
user as brand new and silently orphan their credits, API keys, and history.

**Facts as of 2026-09-13:** 17,694 production users have a `privy_user_id`
(all with real emails), only ~19 active in the last 30 days. 0 linked
wallets, 0 stakes, 0 faucet claims tied to those accounts — nothing
on-chain depends on the old app's embedded wallets, which simplifies the
migration to "just don't lose the account and its credits."

## Design: lazy, server-verified adoption

Rather than a bulk one-time backfill (which would need Privy to hand over
old-DID → new-DID mappings, which it doesn't do), adoption happens lazily,
one login at a time, entirely server-side:

1. **Schema.** `users.privy_app_id text null` — which Privy app a row's
   `privy_user_id` was issued under. Backfilled to the old app id for every
   existing row that already has a `privy_user_id`
   (`supabase/migrations/20260913000000_privy_app_id.sql`). New
   `audit_log` action: `auth.privy_migrated`.

2. **Config** (`src/config/config.py`):
   - `PRIVY_APP_ID` — the *current* app the frontend uses (already existed;
     unchanged in shape).
   - `PRIVY_LEGACY_APP_IDS` — comma-separated list of app id(s) accounts may
     have migrated *from*.
   - `PRIVY_APP_SECRET` — the current app's server secret (Basic-auth
     credential for the Privy API lookup below).
   - `PRIVY_MIGRATION_MODE` — `off` (default) or `adopt`.

3. **Adoption path in `/auth`** (`src/services/privy_migration.py`,
   wired into `src/routes/auth.py`), runs only when **all** of these hold:
   - `PRIVY_MIGRATION_MODE=adopt`.
   - The caller's access token verified against the current app
     (`raw_request.state.privy_token_verified`, set by
     `src/security/privy_token.py`).
   - `get_user_by_privy_id(new_did)` is `None` — no account already has this
     exact DID.

   When those hold, the backend makes a **server-side** call:
   `GET https://auth.privy.io/api/v1/users/{did}` with
   `Authorization: Basic base64(app_id:app_secret)` and header
   `privy-app-id: <app_id>`, 5s timeout. If that returns a user whose linked
   accounts include a verified `email` account or a `google_oauth`/
   `apple_oauth` account (both are only created after the provider's own
   email verification), that email `E` is the candidate.

   The backend then looks for `users` rows with `lower(email) =
   lower(E)` **and** `privy_app_id IN PRIVY_LEGACY_APP_IDS` **and**
   `is_active` not `false`:
   - **Exactly one match → adopt.** Set `privy_user_id = new_did`,
     `privy_app_id = <current app>`; everything else on the row (credits,
     API keys, history) is untouched. Write an `audit_log` row
     (`action="auth.privy_migrated"`, `actor="system"`, metadata carries a
     12-char sha256 prefix of the old and new DID — never the DID or email
     in the clear). Return the normal existing-user response.
   - **Zero matches → normal new-account path.** No error, no special
     response shape — indistinguishable from any other first-time login.
   - **More than one match → do NOT adopt.** Logged + counted; a new
     account is created. Never guess between ambiguous candidates.

4. **Never trust the client for this.** The request body's `email` field
   (or any other client-supplied field) never participates in adoption —
   only Privy's own server response to a lookup keyed by the
   cryptographically verified DID. Every failure mode (Privy API down,
   timeout, malformed response, no adoptable email, ambiguous match) falls
   through to the ordinary new-account path; adoption is a bonus, never a
   requirement for login to succeed. A lookup failure logs
   `privy_migration_lookup_failed` and nothing else — it must never turn
   into a user-visible error.

5. **Wallet linking.** The new app's embedded wallet is ingested by the
   existing `_ingest_privy_wallets` path exactly as any other Privy wallet
   (it's simply a different address than the old app's embedded wallet).
   Old embedded-wallet rows in `user_wallets` (source `privy`, from the old
   app) are left as-is — harmless, they hold nothing on Fuji.

6. **Admin visibility.** `GET /admin/status` gets a `migration` block:
   `{legacy_users, migrated_users, adopt_mode}` — counts of `users` rows by
   `privy_app_id`, plus whether adoption is currently on
   (`src/routes/admin_status.py`, `src/services/privy_migration.py
   ::migration_counts`).

7. **Frontend.** No code change. Only the Vercel env var
   `NEXT_PUBLIC_PRIVY_APP_ID` changes at cut-over. The admin panel's own
   Privy integration (Phase B) starts on the new app from day one, so it
   never needs a migration path of its own.

## Risks

- **Wallet-only logins can't be adopted.** A user whose Privy account has no
  verified email (a wallet-only signup) has no adoptable email — they get a
  new account. Count today: **0** wallet-only accounts among the 17,694
  (all have emails), so this affects nobody at cut-over, but will affect any
  wallet-only signup created between now and the actual switch.
- **Rollback after any adoption is one-directional.** The old Privy app
  must stay alive (not deleted) for at least 30 days post-cutover in case
  of rollback — rolling the three env vars back is easy, but any account
  that was already adopted now carries the *new* app's DID, so a rollback
  would need to run adoption again in reverse. Avoid rolling back once
  `migrated_users > 0` in the `/admin/status` migration block; if a
  rollback is unavoidable at that point, treat it as a fresh migration in
  the opposite direction rather than assuming symmetry.
- **`PRIVY_MIGRATION_MODE=adopt` is meant to stay on indefinitely** — dormant
  users (most of the 17,694, given only ~19 were active in the last 30
  days) will trickle back in over months. There's no "done" state to
  switch it off at; revisit whether it's still needed roughly every 6
  months by checking `legacy_users` in `/admin/status`.

## Cut-over runbook

1. **Merge this backend PR.** CI applies
   `20260913000000_privy_app_id.sql`, backfilling all 17,694 existing rows
   to the old app id. The migration is idempotent (guarded column add,
   guarded backfill `WHERE privy_user_id IS NOT NULL AND privy_app_id IS
   NULL`, guarded index) — safe to re-run.
2. **Railway `api`:** set
   - `PRIVY_LEGACY_APP_IDS=cmg8fkib300g3l40dbs6autqe`
   - `PRIVY_APP_ID=cmtxc6wsn00yn0dle1k5a9bzq`
   - `PRIVY_APP_SECRET=<new app's server secret>` (Railway already holds
     this under `PRIVY_NEW_APP_SECRET` — rename it)
   - `PRIVY_MIGRATION_MODE=adopt`
   - `PRIVY_TOKEN_VERIFICATION=enforce` (adoption requires a verified
     token; the new app's JWKS has exactly one signing key, so enforce mode
     is safe to turn on at the same time)

   Redeploy.
3. **Vercel `gatewayz-frontend`:** set
   `NEXT_PUBLIC_PRIVY_APP_ID=<new app id>`, redeploy. In the new app's Privy
   dashboard, configure login methods (email, Google, wallets), allowed
   domains (`beta.gatewayz.ai`, `admin.gatewayz.ai`, `localhost:3000`), and
   Fuji as the chain.
4. **Verify:**
   - Log in with an email that has an existing (old-app) account → same
     account comes back (same credits, same API keys), an
     `auth.privy_migrated` row appears in `audit_log`, and
     `GET /admin/status`'s `data.migration.migrated_users` reads `1`.
   - Log in with a brand-new email → an ordinary new account, no adoption
     attempted (`legacy_users`/`migrated_users` unchanged for it).
5. **Leave `PRIVY_MIGRATION_MODE=adopt` on indefinitely** (see Risks
   above); there's nothing to "finish" here.

## Files

| Concern | Location |
|---|---|
| Schema | `supabase/migrations/20260913000000_privy_app_id.sql` |
| Config | `src/config/config.py` (`PRIVY_LEGACY_APP_IDS`, `PRIVY_APP_SECRET`, `PRIVY_MIGRATION_MODE`) |
| Adoption logic | `src/services/privy_migration.py` |
| `/auth` wiring | `src/routes/auth.py` (verified-token, unknown-DID branch) |
| Admin visibility | `src/routes/admin_status.py` (`migration` block) |
| Tests | `tests/services/test_privy_migration.py`, `tests/routes/test_auth_privy_migration.py`, `tests/migrations/test_privy_app_id_migration.py` |
| Data-access notes | `docs/security/DATA_ACCESS.md` |
| API notes | `docs/api.md` (`POST /auth/privy`) |
