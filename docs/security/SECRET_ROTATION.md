# Secret Rotation Runbook

**Status:** current as of Phase D (D2) of
`docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md`.
Related: [Data Access — Who Can Read What](./DATA_ACCESS.md),
[Database Migrations](../DATABASE_MIGRATIONS.md),
[Deployment](../DEPLOYMENT.md).

## Where secrets live

| Where | What it holds |
|---|---|
| Railway service **`api`** (this backend) | `ADMIN_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_KEY`, `RESEND_API_KEY`, `PRIVY_APP_ID`, `PRIVY_VERIFICATION_KEY`, `WAYZ_FAUCET_MINTER_PRIVATE_KEY`, `WAYZ_REWARDS_POOL_PRIVATE_KEY`, `STRIPE_SECRET_KEY`, `SENTRY_DSN`, `GATEWAYZ_AUTH_BRIDGE_SECRET`, `UPSTREAM_PSEUDONYM_SECRET` |
| Vercel project **`gatewayz-frontend`** | Only `NEXT_PUBLIC_*` and `NEXTAUTH_*`-type values -- **no service key ever belongs here**, since everything `NEXT_PUBLIC_` ships to the browser |
| Vercel project **`gatewayz-admin`** | `ADMIN_API_KEY` (until Phase B retires it), `SUPABASE_SERVICE_ROLE_KEY`, `ADMIN_TOTP_ENCRYPTION_KEY`, `RESEND_API_KEY`, `NEXTAUTH_SECRET` |

Both Railway and Vercel deploy exclusively from git pushes (see
`docs/DEPLOYMENT.md`) -- rotating a secret always means: set the new value in
the platform's env var UI, then trigger a redeploy through git (a commit, or
`vercel redeploy <url>` for Vercel), never a local CLI push.

`GET /admin/status` (`src/routes/admin_status.py`, `secrets` block, backed by
`src/services/secrets_registry.py`) fingerprints every secret below at
startup and reports `{present, first_seen_at, age_days, rotate_due,
fingerprint_known}` per name -- never the value or the fingerprint itself.
**After rotating any secret, confirm it by watching that secret's
`first_seen_at` reset and `age_days` drop back to 0** on the next
`GET /admin/status` call after redeploy. `rotate_due` flips to `true` once
`age_days >= SECRET_ROTATION_DAYS` (env, default 90).

## General order of operations

For every secret below, unless its section says otherwise:

1. **Mint** the new value (see each section).
2. **Set** it in the platform env var UI (Railway `api` and/or the relevant
   Vercel project) -- do not remove the old value yet.
3. **Redeploy** via git (commit/push, or `vercel redeploy <url>`) so the new
   process picks up the new env var.
4. **Verify** (curl commands per section below), and confirm
   `/admin/status.secrets.<NAME>.first_seen_at` reset.
5. **Revoke** the old value at its source (Supabase dashboard, Stripe
   dashboard, etc.) only after step 4 passes -- revoking first turns a
   rotation into an outage if step 3 silently failed to pick up the new
   value.

## Per-secret runbooks

### `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_KEY`

- **Protects:** full, RLS-bypassing read/write access to every table (see
  `docs/security/DATA_ACCESS.md`) -- the single most powerful credential in
  this system.
- **Set:** Railway `api` -> `SUPABASE_SERVICE_ROLE_KEY` **and**
  `SUPABASE_KEY` -- **both currently hold the service-role key** (not an
  anon key). Also Vercel `gatewayz-admin` -> `SUPABASE_SERVICE_ROLE_KEY`.
  Any place using `SUPABASE_KEY` for a genuinely anon-scoped client would
  need updating separately, but as of this writing none does.
- **Mint:** Supabase Dashboard -> Project Settings -> API -> API keys ->
  regenerate the `service_role` key. This is a full rotation: the old key
  stops working immediately on regeneration (unlike most secrets below,
  there is no independent "revoke" step -- minting a new key already
  invalidates the old one).
- **Order of operations:** because minting == revoking here, do steps 1-3
  (set the new value everywhere it's used, redeploy) as close together as
  possible; there is no safe window to run old and new side by side.
- **Verify:** `curl -s https://api.gatewayz.ai/health` should still return
  200, and `GET /admin/status` (with `ADMIN_API_KEY`) should show
  `integrations.supabase.status: "ok"`.
- **Blast radius if leaked:** total -- RLS bypass on every table. This is
  exactly the incident this rotation guide exists to prevent a repeat of
  (see `Gatewayz - Admin Panel Accounts & Service-Role Leak` in the vault).

### `ADMIN_API_KEY`

- **Protects:** every `require_admin_or_env_key` route (`GET /admin/status`,
  `GET /admin/wayz/status`, GPU provider approve/suspend, etc.) when called
  without a per-user admin key. The admin panel's server-side proxy uses
  this key, never a user's own key, for those calls.
- **Set:** Railway `api` -> `ADMIN_API_KEY`. Vercel `gatewayz-admin` ->
  `ADMIN_API_KEY` (only until Phase B removes it in favor of per-user keys).
- **Mint:** `openssl rand -hex 32`.
- **Verify:** `curl -H "Authorization: Bearer <new-key>" https://api.gatewayz.ai/admin/status`
  returns 200; the old key should then return 401/403 after revocation.
- **Blast radius if leaked:** admin-level access to every
  `require_admin_or_env_key` route, but `gpu_providers.approved_by` is
  `NULL` for actions taken this way (see `docs/security/DATA_ACCESS.md`) --
  actions via this key are not attributable to an individual, which is
  itself part of the reason Phase B retires it from the admin panel.

### `WAYZ_FAUCET_MINTER_PRIVATE_KEY`

- **Protects:** the wallet authorized to mint WAYZ faucet claims on-chain.
- **Set:** Railway `api` -> `WAYZ_FAUCET_MINTER_PRIVATE_KEY`.
- **Mint:** generate a fresh wallet with `cast wallet new`.
- **Order of operations (non-trivial):** the new wallet has no on-chain
  minter role yet.
  1. `cast wallet new` -> new address + private key.
  2. Re-grant the on-chain minter role to the new address via
     `gatewayz-protocol/script/GrantMinter.s.sol` (separate repo) --
     **do this before** setting the new key in Railway, or faucet claims
     will fail between steps.
  3. Set `WAYZ_FAUCET_MINTER_PRIVATE_KEY` in Railway, redeploy.
  4. Verify a faucet claim succeeds end to end.
  5. Revoke the old wallet's minter role on-chain (separate transaction).
- **Blast radius if leaked:** attacker can mint faucet WAYZ at will -- direct
  token-supply/financial exposure, not just data exposure.

### `WAYZ_REWARDS_POOL_PRIVATE_KEY`

- **Protects:** the wallet holding the WAYZ rewards pool balance used for
  GPU settlement payouts.
- **Set:** Railway `api` -> `WAYZ_REWARDS_POOL_PRIVATE_KEY`.
- **Mint:** `cast wallet new`.
- **Order of operations (non-trivial, flagged explicitly):** unlike the
  minter key, this wallet's authority is its *token balance*, not an
  on-chain role grant -- rotating it means **moving the entire rewards pool
  token balance** from the old wallet to the new one before the old key is
  revoked, and settlement jobs must be paused for the transfer window (see
  `src/services/gpu/settlement.py` schedulers in `src/services/startup.py`).
  Treat this as a planned maintenance window, not a routine rotation:
  1. Pause the GPU settlement scheduler.
  2. `cast wallet new` -> new address + private key.
  3. Transfer the full WAYZ balance from the old wallet to the new one
     on-chain; confirm the transaction.
  4. Set `WAYZ_REWARDS_POOL_PRIVATE_KEY` in Railway, redeploy.
  5. Resume the settlement scheduler; verify one settlement cycle succeeds.
  6. Only then treat the old private key as revoked (it now controls an
     empty wallet, so "revocation" here just means discarding it securely).
- **Blast radius if leaked:** attacker can drain the entire rewards pool
  balance -- direct financial loss.

### `RESEND_API_KEY`

- **Protects:** outbound transactional email (invites, password resets,
  ops alerts) via `src/services/email.py`.
- **Set:** Railway `api` -> `RESEND_API_KEY`. Vercel `gatewayz-admin` ->
  `RESEND_API_KEY`.
- **Mint:** Resend Dashboard -> API Keys -> Create API Key.
- **Verify:** `GET /admin/status` -> `integrations.resend.status` should be
  `"ok"` (a `401`/`403` there means the new key is wrong or not yet
  redeployed; the account itself has been seen `degraded`/suspended before
  -- see the Phase A vault note).
- **Blast radius if leaked:** attacker can send email as this Resend
  account/domain (phishing risk), and can read domain configuration via the
  Resend API. No direct data exposure from this backend's own tables.

### `PRIVY_VERIFICATION_KEY` (and `PRIVY_APP_ID`)

- **Protects:** server-side verification that a Privy auth token actually
  belongs to the claimed user (`src/security/privy_token.py`) -- the
  account-takeover vector closed by requiring real verification
  (gatewayz-backend#2248).
- **Set:** Railway `api` -> `PRIVY_APP_ID`, `PRIVY_VERIFICATION_KEY`.
- **Mint:** Privy Dashboard -> regenerate the verification key for the app.
  `PRIVY_APP_ID` itself does not need rotating unless the whole Privy app is
  recreated.
- **Verify:** `GET /admin/status` -> `integrations.privy.status` should be
  `"ok"` (this check parses the PEM key locally, it does not call Privy).
  Then confirm a real `/auth` call with a live Privy token still succeeds.
- **Blast radius if leaked:** the verification key is a *public* key by
  design (it verifies signatures, it cannot forge them) -- leaking it alone
  does not grant token forgery. Rotate anyway if exposure is suspected,
  since Privy dashboard access or key confusion elsewhere could change that
  assumption.

### `STRIPE_SECRET_KEY`

- **Protects:** payment processing (`src/services/payments.py` and related).
- **Set:** Railway `api` -> `STRIPE_SECRET_KEY`.
- **Mint:** Stripe Dashboard -> Developers -> API keys -> roll the secret
  key (Stripe supports a rolling grace period where both old and new keys
  work briefly -- use it rather than an instant cutover).
- **Verify:** `GET /admin/status` -> `secrets.STRIPE_SECRET_KEY.present`
  should be `true` (the integrations check only reports "configured", it
  does not call Stripe live -- see `_check_stripe` in
  `src/services/integrations_health.py`); confirm with a real test-mode
  charge or Stripe's dashboard "API key last used" timestamp.
- **Blast radius if leaked:** full Stripe account access at whatever
  restriction level the key has (charges, refunds, customer data) --
  rotate immediately and check the Stripe dashboard's request log for
  unrecognized activity.

### `SENTRY_DSN`

- **Protects:** nothing sensitive on its own -- a DSN only lets a caller
  *send* events to this Sentry project, it grants no read access. Rotate it
  if it's being abused to flood the project with junk events.
- **Set:** Railway `api` -> `SENTRY_DSN`.
- **Mint:** Sentry Dashboard -> Project Settings -> Client Keys (DSN) ->
  create a new key, then delete the old one from the Sentry side once
  traffic has moved over.
- **Verify:** `GET /admin/status` -> `integrations.sentry.status: "ok"`;
  trigger a test error and confirm it appears in the new DSN's project feed.
- **Blast radius if leaked:** low -- event-flooding/noise, not data
  exposure (Sentry payloads should never contain secrets or raw user data
  per this backend's own scrubbing, but a leaked DSN alone doesn't expose
  anything already captured).

### `GATEWAYZ_AUTH_BRIDGE_SECRET`

- **Protects:** the shared secret used between the frontend and this
  backend to authenticate the auth-bridge exchange between them -- **both
  sides must be updated together**, or the bridge breaks immediately (this
  is a shared-secret, not a public/private keypair).
- **Set:** Railway `api` -> `GATEWAYZ_AUTH_BRIDGE_SECRET`, and the matching
  frontend project's equivalent server-side env var (never a `NEXT_PUBLIC_`
  one).
- **Mint:** `openssl rand -hex 32`.
- **Order of operations:** unlike most secrets here, there is no safe
  "old and new both work" window unless the bridge code is written to
  accept two secrets during rollover -- confirm with whoever owns the
  frontend's auth-bridge code before rotating in production; otherwise
  treat this as a synchronized deploy on both sides (set both, redeploy
  both, verify, only then treat the old value as revoked).
- **Verify:** a real cross-service auth-bridge call succeeds end to end
  after both sides redeploy.
- **Blast radius if leaked:** attacker can forge the frontend<->backend
  bridge handshake -- treat as a full trust-boundary breach between the two
  services.

### `UPSTREAM_PSEUDONYM_SECRET`

- **Protects:** the per-request pseudonym sent upstream (as `user`) to
  providers' own abuse detection when `UPSTREAM_ABUSE_PSEUDONYM=true`
  (`src/services/upstream/anonymize.py`) -- an HMAC key, not a feature flag;
  `UPSTREAM_ABUSE_PSEUDONYM` itself is just the boolean that turns this
  behavior on and holds no secret material. `src/services/startup.py`'s
  `_validate_upstream_pseudonym_config` fails startup loudly if the flag is
  on and this secret is missing or under 32 characters, rather than raising
  on every chat completion.
- **Set:** Railway `api` -> `UPSTREAM_PSEUDONYM_SECRET`.
- **Mint:** `openssl rand -hex 32` (or longer -- the only requirement
  enforced at startup is >=32 characters).
- **Order of operations:** simpler than most secrets here -- there is no
  "revoke the old value" step. Set the new value, redeploy. Pseudonyms are
  HMAC-derived per request and never stored, so rotating this secret simply
  means every pseudonym computed after the redeploy stops correlating with
  ones computed before it -- which is often the point of rotating it (to
  break any accumulated cross-request correlation), not a side effect to
  work around.
- **Verify:** `GET /admin/status` -> confirm
  `secrets.UPSTREAM_PSEUDONYM_SECRET.first_seen_at` reset after redeploy.
  There is no live integration check for this one (it's never called
  out-of-process) -- a chat completion succeeding with
  `UPSTREAM_ABUSE_PSEUDONYM=true` set is the functional verification.
- **Blast radius if leaked:** an attacker who knows this secret could
  compute the same pseudonym Gatewayz would for a given request and
  potentially correlate/deanonymize traffic upstream providers see -- see
  `docs/security/ANONYMITY_THREAT_MODEL.md` for the full threat model this
  secret is part of.

## Never

- **Never `vercel deploy --prod` from a local checkout.** It uploads
  whatever is on disk right now -- another agent's uncommitted files, a
  stale branch, a half-finished edit -- as the production build. This has
  already caused an outage once (detaching the `admin.gatewayz.ai` alias,
  ~5 min of `DEPLOYMENT_NOT_FOUND`; see the Phase A vault note and
  `docs/DEPLOYMENT.md`). Deploy only via git; use `vercel redeploy <url>` to
  rebuild an existing deployment.
- **Never put a service key in a `NEXT_PUBLIC_` var.** Anything prefixed
  `NEXT_PUBLIC_` ships into the browser bundle -- this is exactly how the
  `SUPABASE_SERVICE_ROLE_KEY` leak happened (see
  `docs/security/DATA_ACCESS.md` and the Phase A vault note).
- **Never commit `.env`.** Rotate immediately, as a leaked value not a
  routine rotation, if one ever is.
