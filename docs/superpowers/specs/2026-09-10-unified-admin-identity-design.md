# Unified identity & admin architecture — design + plan

**Date:** 2026-09-10 · **Status:** proposed · **Owner:** arminrad
**Repos:** gatewayz-backend (`api`, Railway), gatewayz-frontend (beta.gatewayz.ai, Vercel), gatewayz-admin (admin.gatewayz.ai, Vercel), gatewayz-protocol.

## 1. Problem

Three apps, three identity systems, three places holding production secrets:

| Surface | Identity | Secrets held | Incidents this week |
|---|---|---|---|
| Gatewayz users (frontend) | Privy → `POST /auth` → per-user `gw_live_` API key | none server-side (Vercel only public vars) | — |
| Backend | API key (`get_current_user`), `users.role`/`is_admin`; Privy token verify (M2) | all (Supabase service role, provider keys, WAYZ signers) | one route-signature break (#2281) |
| Admin panel | its own `admin_users` table + NextAuth password (+ TOTP as of #81) | **was**: a live admin API key in 89 files + the Supabase **service-role JWT in the browser bundle**; now: service-role key server-side + `ADMIN_API_KEY` | key leak ×2, suspended Resend key discovered, `*.sql` gitignored so schema drifted |

Consequences: nobody can log into the panel with their Gatewayz account; admin actions are attributed to a shared key, not a person; email is configured in two places (one broken); the panel owns a DB schema it cannot version; every panel deploy is one env var away from leaking prod.

## 2. Target architecture

```
                 ┌──────────────── Privy (one IdP: email, Google, passkey, wallet) ────────────────┐
                 │                                                                                  │
   beta.gatewayz.ai/*  ──Privy token──▶  POST /auth  ──▶ per-user API key (httpOnly cookie)   ◀──  beta.gatewayz.ai/admin/*
        (users)                                   │                                                 (staff, role-gated)
                                                  ▼
                                 gatewayz-backend (Railway `api`)
                     • the ONLY holder of DB/service secrets
                     • `users.role ∈ {user, admin, superadmin}` is the single RBAC source
                     • all admin reads/writes: /admin/* with require_admin / require_superadmin
                     • audit log, invites, email (Resend), migrations, job registry, status
```

Principles:
1. **One identity.** Every human is a `users` row; staff are rows with `role='admin'|'superadmin'`. Privy is the IdP for everyone; its MFA (passkeys/OTP) is the second factor. The TOTP system from #81 becomes the break-glass path only.
2. **Per-user credentials for admin actions.** The panel calls the backend with the *admin's own* API key (obtained via `/auth` after Privy login, held in an encrypted httpOnly session cookie). `ADMIN_API_KEY` survives only for cron/service calls and is not present on Vercel.
3. **Backend owns data.** The admin surface holds **no** Supabase client, no migrations, no email provider. `admin_users`, `admin_auth_tokens`, `admin_audit_log` move into the backend as `users` + `admin_invites` + `audit_log`.
4. **One Next app.** The admin panel becomes the `(admin)` route group in gatewayz-frontend at `/admin/*`, sharing the Privy provider, design system, CI, and deploy. gatewayz-admin is archived.
5. **Secrets stay on Railway; CI proves it.** A build step fails any Vercel app whose `.next/static` or source contains a JWT (`eyJ…`), `gw_…`, `re_…`, `sbp_…`, `sk-…`, or a Supabase URL.

## 3. Phases

### Phase A — Backend owns admin identity (≈3 days, no user-visible change)
Goal: everything the panel needs exists as backend API; the panel can stop touching Supabase.

A1. **Roles.** Migration: `users.role` check constraint `('user','admin','superadmin')`; backfill `superadmin` for the current `admin_users.superadmin` emails; `is_admin` kept in sync by trigger (deprecate later). `src/security/deps.py`: `require_superadmin`; `require_admin` accepts both. `ip_whitelist.py` already expects `superadmin`.
A2. **Staff management API** (`src/routes/admin_staff.py`, superadmin): `GET /admin/staff`, `POST /admin/staff/invite {email, role}` (creates/updates the `users` row's role + `admin_invites` token, emails via Resend), `PATCH /admin/staff/{user_id} {role|status}`, `DELETE /admin/staff/{user_id}` (role→user), `POST /admin/staff/{user_id}/revoke-keys`. Protections: last-superadmin, no self-demotion. All writes → `audit_log`.
A3. **Audit log** (`audit_log` table; `src/db/audit.py::record(actor_user_id, action, target, ip, ua, metadata)`; `GET /admin/audit?limit`). Wire into: staff changes, GPU approve/suspend, payout-rate edits, config flips, key revocations, `/auth` logins with role≥admin.
A4. **Email in one place.** `src/services/email.py` (Resend) with templates `staff_invite`, `password_reset` (for break-glass), `provider_approved`; health check in `/admin/wayz/status` → generalize to `GET /admin/status` (jobs + integrations: Resend, Privy, Fuji RPC, Supabase; secrets presence/age, never values).
A5. **`/auth` returns role** and the panel-compatible fields; `require_privy_verified` flag so admin routes can demand a token-verified session (`request.state.privy_token_verified`, M2) — env-gated until `PRIVY_TOKEN_VERIFICATION=enforce`.
A6. **Migrate `admin_users` → `users`** (one-off script `scripts/migrate_admin_users.py`): match by email; create `users` rows for staff without one (`did:staff:<uuid>` placeholder until they log in via Privy, which links by email); copy `role`; emit report. Keep `admin_users` read-only for 30 days, then drop.
Acceptance: panel flows can be executed via `curl` with a superadmin API key only; `tests/routes/test_admin_staff.py`, `tests/security/test_rls_policies_static.py` extended to the new tables; no new anon grants.

### Phase B — Panel logs in with Gatewayz identity (≈2 days; needs Privy App ID + verification key)
B1. Admin panel (still separate repo, to de-risk): add Privy provider (`@privy-io/react-auth`) with the same app id as the frontend; on login → `POST /auth` → API key + role → NextAuth session stores the key (encrypted JWT cookie); role<admin → `/unauthorized`.
B2. All `/api/proxy/*` routes send the **session user's** key; `getAdminApiKey()` deleted; `ADMIN_API_KEY` removed from Vercel.
B3. `/admin-users` page rewired to `/admin/staff`; `/account` shows Privy-managed security (link to Privy MFA) + break-glass TOTP for the fallback login; delete `src/lib/server/supabase-admin.ts`, `SUPABASE_*` env removed from Vercel.
B4. CI secret-in-bundle scan (shared GitHub Action `alpaca-network/actions/bundle-secret-scan`) added to gatewayz-admin and gatewayz-frontend.
Acceptance: Vercel env for the panel = `NEXTAUTH_SECRET`, `NEXTAUTH_URL`, `NEXT_PUBLIC_PRIVY_APP_ID`, `NEXT_PUBLIC_API_BASE_URL` only; audit log shows real actor ids for every admin action.

### Phase C — Fold the panel into the frontend (≈1 week)
C1. `src/app/(admin)/admin/**` route group in gatewayz-frontend; middleware gates by `role` from the session; nav entry visible to admins only.
C2. Port pages in dependency order: status/ops (`/admin/ops` = WAYZ Ops + system health), staff, users, providers/models, GPU approvals, analytics, pricing, rate limits, coupons/plans. Each page = thin client of an existing backend route; delete the corresponding `/api/proxy` route (frontend calls backend directly with the cookie-held key via the existing `build-auth-request`).
C3. Move shared components into `src/components/admin/*`; reuse the frontend design system; drop the panel's custom UI kit.
C4. Cut over `admin.gatewayz.ai` → redirect to `beta.gatewayz.ai/admin`; archive gatewayz-admin (read-only) after 2 weeks of parallel run.
Acceptance: one Vercel project serves both; `npm test` covers admin pages; Playwright smoke: login → `/admin/ops` → approve a test GPU provider → audit row.

### Phase D — Hygiene that makes it stay stable (parallel with C)
D1. Backend: `scripts/check_env_secrets.py` at startup → `/admin/status.secrets` reports presence + age per secret (rotate reminders at 90 d).
D2. Rotation runbook `docs/security/SECRET_ROTATION.md` (Supabase service role, ADMIN_API_KEY, WAYZ signers, Resend, Privy) with the exact Railway/Vercel var names.
D3. Migrations only in gatewayz-backend; `supabase/migrations` applied by CI (`supabase db push` with a CI-only DB role) instead of by hand via the Management API.
D4. Deploy rule enforced: Vercel "Git only" (disable CLI prod deploys via project setting *Deployment Protection → require Git*); Railway deploys via GitHub only (already).

## 4. Decisions & trade-offs
- **Why the user's own API key rather than forwarding the Privy token to every admin route:** the backend's auth core is API-key based; `/auth` already exchanges a Privy token for a key; per-user keys give attribution/revocation without touching 55 route modules. Privy-token-per-request can come later behind `require_privy_verified`.
- **Why fold into the frontend rather than keep two apps:** one Privy config, one auth context (already consolidated in `src/lib/auth`), one CI with the secret scan, one design system; the panel's ~28 pages are all thin proxies. Blast radius is mitigated by the route group's own test job and Vercel preview gates.
- **Why keep the TOTP/password path at all:** break-glass when Privy is down or misconfigured (it is not configured today). Limited to `superadmin`, audited, and its login page is not linked from the UI.
- **What we drop:** `admin_users` (after 30 d), `ADMIN_API_KEY` on Vercel, `SUPABASE_*` on Vercel, the panel's Resend config, the panel's `/api/proxy` layer (Phase C), `gatewayz-admin` repo (archived).

## 5. Risks
- Privy creds not available → Phase B blocks; A and D proceed. Mitigation: Google SSO on the panel (needs only an OAuth client) as an interim provider mapped to `users` by email.
- Staff without a Gatewayz account → placeholder `users` rows; first Privy login links by verified email (M2 collision rules already forbid re-binding a DID — invite flow must create the row *before* the first login or let `/auth` adopt a placeholder by verified email; decide in A6, test both).
- Backend `get_user()` ignores `is_active` on `api_keys_new` (found 2026-09-09) — fix in A1 so key revocation is real.
- Suspended Resend key — invites/resets silently fail until fixed; `/admin/status` must show `email: degraded`.

## 6. Sequence & sizing
A (3 d) → B (2 d, gated on Privy creds; Google interim) → C (5–7 d) ∥ D (2 d). Total ≈ 2.5 weeks of agent time; human inputs: Privy App ID + verification key, Google OAuth client, Resend account fix, Supabase service-role rotation.
