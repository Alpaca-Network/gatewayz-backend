# Wallet-Based Identity & Auth — Design (Milestone 2)

**Issues:** Alpaca-Network/gatewayz-backend #2248 (epic), #2249, #2250, #2251, #2252, #2253, #2254.
**Repos:** gatewayz-backend (FastAPI/Supabase), gatewayz-frontend (Next.js 15 / Privy).
**Research input:** the M2 research doc (session scratchpad) — every "today" statement below was verified against source on 2026-09-03.
**Related:** WAYZ Milestone 1 (staking indexer, faucet, `/staking` dashboard) deferred wallet↔account linkage to this milestone.

## 1. Problem and goals

Gatewayz identity today is: Privy login in the browser → `POST /auth` with a **client-supplied** Privy user object → the backend creates/looks up the user by that id and returns an API key → every request authenticates with `Authorization: Bearer <api_key>`. Wallets are stripped by the frontend before the sync, so connecting a wallet has no effect on the account. There is no JWT/session infrastructure; "anonymous" is a hashed-IP daily bucket in Redis with no persistent entity.

Goals (north star: wallets are users):
1. A wallet can *be* an account: sign in / sign up with nothing but a signature (#2249, #2250, #2252).
2. Existing accounts can link wallets, one wallet ↔ one account (#2251).
3. Guests get a persistent, upgradeable identity (#2253).
4. One dependency chain answers "who is this request from" for API-key, wallet and guest callers, backward compatible (#2254).

Non-goals: smart-contract wallets (EIP-1271), Solana/Bitcoin wallets, making `users.email` nullable, replacing API keys with JWTs, enforcing staking allowances on inference (follow-up once wallets are linked).

## 2. Prerequisite: the backend must verify Privy tokens (W0)

**Finding.** `POST /auth` trusts `request.user.id`. Anyone who knows (or guesses) a Privy DID can obtain that account's primary API key; the only defence is 10 attempts / 15 min / IP. `PrivyAuthRequest.token` is carried but never checked. `PyJWT[crypto]` is already in `requirements.txt` and unused.

**Decision.** Verify the Privy **access token** server-side before touching any account:
- New module `src/security/privy_token.py`: `verify_privy_access_token(token: str) -> PrivyTokenClaims` using PyJWT, algorithm **ES256**, `issuer="privy.io"`, `audience=Config.PRIVY_APP_ID`, key from `Config.PRIVY_VERIFICATION_KEY` (PEM, from the Privy dashboard). Claims used: `sub` (Privy DID), `sid`, `exp`. Clock skew 60s. (If a JWKS URL is preferred later, the module boundary stays.)
- `/auth` rule: the token's `sub` MUST equal `request.user.id`, else `401 invalid_privy_token`. Missing token → `401 privy_token_required`.
- Rollout switch `Config.PRIVY_TOKEN_VERIFICATION = "enforce" | "log" | "off"` (default **`enforce`** when `PRIVY_VERIFICATION_KEY` is set, otherwise `log`; `off` only for tests). In `log` mode the request proceeds but a structured warning `privy_token_unverified reason=…` is emitted so the frontend rollout can be watched for a day before flipping to `enforce`.
- Wallet linked-accounts from Privy are ingested into `user_wallets` (§4) **only when the token verified**. Unverified claims never create identity.
- Frontend (W3): always obtain the access token before syncing (retry `getAccessToken()` up to 3× with backoff; never "continue without token"), in both the auth context and the cross-domain `auth-sync.ts` path.

## 3. Identity model

```
users (existing)                      user_wallets (new)
  id            ─────────────────┐     id              bigserial PK
  email  NOT NULL UNIQUE          └──►  user_id         bigint FK users(id) ON DELETE CASCADE
  privy_user_id                        wallet_address  text NOT NULL UNIQUE   -- lower-case 0x + 40 hex
  auth_method  ('privy'|'wallet'|…)    chain_namespace text NOT NULL DEFAULT 'eip155'
  …                                    source          text NOT NULL CHECK (source IN ('privy','siwe'))
                                       wallet_client_type text            -- privy|metamask|… (informational)
                                       is_primary      boolean NOT NULL DEFAULT false
                                       verified_at     timestamptz NOT NULL DEFAULT now()
                                       created_at      timestamptz NOT NULL DEFAULT now()
  UNIQUE (user_id, wallet_address); partial unique index ON user_wallets(user_id) WHERE is_primary; RLS enabled, no policy (service-role only).
```
- A user may have several wallets (Privy embedded + external); a wallet belongs to exactly one user (`wallet_address UNIQUE`). Addresses are normalised to lower-case at every boundary (same validator as the faucet).
- Wallet-only users reuse the shipped placeholder-email pattern: `wallet+{address}@wallet.placeholder`; username `wallet_{first 6 hex after 0x}` (+ suffix on collision). `auth_method='wallet'`, `credits=0`, `subscription_status='inactive'` — identical provisioning to Privy signups today.
- `wallet_stakes` stays keyed by wallet address; a later follow-up can join it to `user_wallets`.

## 4. Backend endpoints (W1)

All under `src/routes/wallet_auth.py`, registered as `("wallet_auth", "Wallet Auth")` in `src/main.py`. All addresses validated by `^0x[0-9a-fA-F]{40}$` and lower-cased (extract the faucet's validator into `src/utils/wallet_address.py` and use it in both places). Signature field `min_length=1, max_length=200`.

### 4.1 Message format — SIWE (EIP-4361), built server-side, signed verbatim
```
{domain} wants you to sign in with your Ethereum account:
{address (EIP-55 checksummed)}

{statement}

URI: {uri}
Version: 1
Chain ID: {chain_id}
Nonce: {nonce}
Issued At: {issued_at ISO-8601 Z}
Expiration Time: {issued_at + 300s}
```
`domain`/`uri` from `Config.SIWE_DOMAIN` (default `gatewayz.ai`) / `Config.SIWE_URI` (default `https://gatewayz.ai`); `chain_id` defaults to `43113` (Avalanche Fuji) and MUST be one of `Config.SIWE_ALLOWED_CHAIN_IDS` (default `43113,43114`). Statements: login → `Sign in to Gatewayz.`; link → `Link this wallet to Gatewayz account {user_id}.`. The **server stores the exact message** in Redis keyed `siwe_nonce:{purpose}:{address}` (TTL 300s) and the verify step compares the client-submitted message byte-for-byte to the stored one before recovering the signer — the client never gets to choose the message contents. Nonce = `secrets.token_hex(16)`; consumed atomically with `getdel`.

Verification: `eth_account.Account.recover_message(encode_defunct(text=message), signature=sig)` → recovered address must equal the claimed address (case-insensitive). EOA only; put this behind `verify_wallet_signature(address, message, signature) -> bool` in `src/security/wallet_signature.py` so EIP-1271 can be added later without touching routes. Every eth_account call gets a test with the **real** library (see §8).

### 4.2 Sign-in / sign-up (unauthenticated) — #2249, #2250, #2252
- `POST /auth/wallet/nonce` `{wallet_address, chain_id?}` → `{"success":true,"data":{"message":"…","expires_in":300}}`. Rate limit: new `AuthRateLimitType.WALLET_NONCE` (20 / 15 min / IP) via `check_auth_rate_limit`. Response is identical whether or not the wallet is registered (no enumeration).
- `POST /auth/wallet/verify` `{wallet_address, message, signature}` → on success returns **the same shape as `POST /auth`** (`PrivyAuthResponse`: `api_key`, `user_id`, `username`, `email`, `credits`, `is_new_user`, …) so the frontend and cross-domain transfer need no new client code. Rate limits: `LOGIN` (10 / 15 min / IP); the new-user branch additionally runs `REGISTER` (3 / hour / IP).
  - Errors: 400 `nonce_missing_or_expired` (also when message ≠ stored), 401 `invalid_signature`, 401 `signature_address_mismatch`, 429.
  - Existing wallet → look up `user_wallets` → return that user's primary active API key (reuse `_handle_existing_user` from `auth.py`).
  - Unknown wallet → create user (§3), `create_api_key(user_id, key_name="Wallet Sign-In", environment_tag=resolve_key_environment(...), expiration_days=Config.WALLET_SESSION_KEY_DAYS (default 30), is_primary=True)`, insert `user_wallets(source='siwe', is_primary=true)`. **"Session" = an expiring API key** — no JWT infra is introduced (#2252's premise was false; this gives expiry/refresh-by-re-signing with zero new middleware). Re-signing after expiry returns a fresh key via the existing-user branch when the primary key is expired/inactive (extend `_handle_existing_user` to mint a replacement in that case).

### 4.3 Link / list / unlink (authenticated via `get_user_id`) — #2251
- `POST /auth/wallet/link/nonce` `{wallet_address, chain_id?}` → message with the link statement. Rate limit `create_endpoint_rate_limit("wallet_link_nonce", 10, 60)`.
- `POST /auth/wallet/link` `{wallet_address, message, signature}` → 200 `{"success":true,"data":{wallet}}`; 409 `wallet_linked_to_other_account` if `wallet_address` belongs to another user (never reveal which); idempotent 200 if already linked to the caller. First linked wallet becomes primary.
- `GET /auth/wallets` → `{"success":true,"data":{"wallets":[{wallet_address, source, wallet_client_type, is_primary, verified_at}]}}`.
- `DELETE /auth/wallets/{wallet_address}` → 200; 400 `last_auth_method` when the user's `auth_method=='wallet'` and this is their only wallet (they would be locked out).

### 4.4 `/auth` (Privy) changes — W0 + W1b
After token verification passes, for each `linked_accounts[]` entry with `type in ('wallet','smart_wallet')` and `chain_type=='ethereum'` (frontend will now send them; schema already accepts `address`), upsert `user_wallets(source='privy', wallet_client_type=account.wallet_client_type)`. If the address is already owned by **another** user: skip it and log `wallet_conflict` (do not fail the login — the user can resolve via the link flow's 409).

### 4.5 DB module `src/db/user_wallets.py`
`get_wallets_for_user(user_id)`, `get_wallet(address) -> row|None`, `link_wallet(user_id, address, source, wallet_client_type=None, make_primary=False) -> row|None` (returns None on conflict — caller checks `get_wallet` first to distinguish), `unlink_wallet(user_id, address) -> bool`, `count_wallets(user_id)`. Existing try/except → `logger.warning` → safe-default convention. Migration `supabase/migrations/20260903000000_user_wallets.sql`, idempotent.

## 5. Request identity — single source of truth (W2) — #2254

New `src/security/identity.py`:
```python
@dataclass(frozen=True)
class RequestIdentity:
    kind: Literal["api_key", "anonymous"]      # how the caller authenticated at the transport level
    user_id: int | None
    api_key: str | None
    auth_method: str | None                    # users.auth_method: privy | wallet | email | …
    is_guest: bool                             # users.auth_method == 'wallet' and no payment signal, or privy guest flag persisted
    wallet_addresses: tuple[str, ...]          # from user_wallets (cached 60s with the user lookup)
```
`get_request_identity(...) -> RequestIdentity` composes the existing `get_optional_api_key` + user lookup + `get_wallets_for_user`. It does **not** replace `get_user_id`/`get_api_key` (backward compatibility is the requirement) — it is the one place that knows all three shapes. `chat.py` swaps its ad-hoc `is_anonymous = api_key is None` for `identity.kind == "anonymous"`, behaviour unchanged. Tests cover the three paths (API key user, wallet-linked user, anonymous) against the real dependency chain via TestClient. No new middleware module — the codebase's pattern is `Depends()` chains.

`users.auth_method` gains the value `'wallet'` (no schema change; it is free text today). Privy guest accounts arrive through `/auth` with `is_guest=true`; we persist nothing special for them beyond `auth_method='privy'` — guests are ordinary 0-credit accounts.

## 6. Frontend (W3a now, W3b after)

**W3a — wallets become part of the account**
1. Collapse the duplicated auth-body builders (`gatewayz-auth-context.tsx:728-761` and `integrations/privy/auth-sync.ts:66-141`) into one `src/lib/auth/build-auth-request.ts` used by both; stop filtering `wallet`/`smart_wallet` linked accounts (send `type, address, chain_type, wallet_client_type, verified_at`); always send the Privy access token (retry ×3, surface a hard error instead of syncing without it).
2. `/login` and `/signup`: "Continue with wallet" as a first-class button → `login({loginMethods:['wallet']})`; onboarding copy for wallet-only accounts (0 credits, free models available, buy credits to unlock paid models). Desktop/Tauri: hidden (Privy not mounted).
3. Settings → **Wallets** section: list from `GET /auth/wallets`; "Link wallet" = connect via Privy (`connectWallet()`) then `POST /auth/wallet/link/nonce` → `useActiveWallet().signMessage(message)` → `POST /auth/wallet/link` (explicit, works for external wallets); unlink with confirm; show primary badge; map 409/400 to copy. Typed client `src/lib/auth/wallet-auth-api.ts` (Bearer via `makeAuthenticatedRequest`).
4. Tests for the shared builder (wallet accounts included, token required), the settings section states, and the API client error mapping.

**W3b — guests (#2253)** *(after W3a merges)*
Use Privy guest accounts: on first visit to chat when not authenticated, `useGuestAccounts().createGuestAccount()` (only when `NEXT_PUBLIC_PRIVY_GUEST_ACCOUNTS=true`) → the normal sync creates a 0-credit account with an embedded wallet → free models work with a persistent identity; "Sign in to keep this account" upgrades in place via Privy's linking (`useLinkAccount`), same Privy user id → same backend account. The IP-hash anonymous limiter stays for callers with no account at all. Kill switch: the env flag.

## 7. Security notes
- Nonce is bound to purpose + address; message is server-authored and byte-compared; one-time use; 5-minute expiry; chain id allow-listed; domain/URI fixed → phishing signatures from other dapps don't verify here.
- Unauthenticated nonce endpoint is IP-rate-limited and non-enumerating. Verify endpoint shares `LOGIN`/`REGISTER` limits with the Privy path so wallet signup cannot farm accounts faster than email signup.
- New wallet-first keys pass through `resolve_key_environment()` like every other signup (no-payment-signal accounts get `test` keys).
- Never log signatures or full tokens; log addresses only lower-cased.
- Privy token verification failure modes are explicit (`expired`, `bad_signature`, `sub_mismatch`, `missing`) and counted in Prometheus.

## 8. Testing rules (binding)
- Every `eth_account`/PyJWT call has at least one test using the **real** library object (sign with a throwaway `Account.create()` key in tests; mint a real ES256 JWT with a test key pair for the Privy verifier). No `MagicMock` as the sole proof of an external API's shape — four bugs shipped that way in the last week.
- Route tests go through the FastAPI app (`TestClient`), mocking `src.db.*` functions, mirroring `tests/routes/test_faucet.py`.
- Backward-compat tests: existing `POST /auth` happy path in `log` mode; API-key auth unchanged; anonymous chat gating unchanged.

## 9. Config additions
`PRIVY_APP_ID`, `PRIVY_VERIFICATION_KEY`, `PRIVY_TOKEN_VERIFICATION` (enforce|log|off), `SIWE_DOMAIN`, `SIWE_URI`, `SIWE_ALLOWED_CHAIN_IDS`, `WALLET_SESSION_KEY_DAYS` (30). Frontend: `NEXT_PUBLIC_PRIVY_GUEST_ACCOUNTS`.

## 10. Rollout
1. Merge W0 with `PRIVY_TOKEN_VERIFICATION=log`; set `PRIVY_APP_ID`/`PRIVY_VERIFICATION_KEY` on Railway; deploy W3a (frontend always sends the token); watch `privy_token_unverified` for 24h; flip to `enforce`.
2. Merge W1 (endpoints + migration), W2 (identity), then W1b (Privy wallet ingestion) and W3b (guests).
3. Follow-ups (not M2): EIP-1271; `GET /staking/me` using linked wallets; allowance enforcement; faucet eligibility via linked wallets.

## 11. Workstreams and issue mapping
| WS | Repo | Scope | Issues |
|---|---|---|---|
| W0 | backend | Privy access-token verification + rollout switch | prerequisite for #2254 |
| W1 | backend | migration, `user_wallets` db module, SIWE nonce/verify, link/list/unlink, shared address validator/signature verifier | #2249 #2250 #2251 #2252 |
| W1b | backend | `/auth` ingests verified Privy wallets | #2251 |
| W2 | backend | `RequestIdentity` + `get_request_identity`, chat swap, 3-path tests | #2254 |
| W3a | frontend | shared auth body, send wallets + token, Continue-with-wallet, Settings→Wallets | #2250 #2251 |
| W3b | frontend | Privy guest accounts + upgrade CTA | #2253 |
