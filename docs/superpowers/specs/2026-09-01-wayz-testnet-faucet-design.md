# WAYZ Testnet Faucet — Design

Status: draft, pending human review
Repo: Alpaca-Network/gatewayz-backend
Related: gatewayz-backend#2245; WAYZToken.sol merged in
Alpaca-Network/gatewayz-protocol PR #1; WAYZ staking indexer merged in
gatewayz-backend PR #2268

## Context

The issue asks for a claim endpoint gated by an "existing genesis-points
balance." That system doesn't exist in this backend (confirmed by
research: zero hits for "genesis points" anywhere in the codebase — it's
a concept from the pitch deck / token.gatewayz.ai, never implemented
here). Per human decision, eligibility is instead gated on existing usage
data already tracked in `usage_records` — a low bar (≥1 completed
request), enough to prove the account isn't a pure sign-up-and-claim bot,
without inventing a new points subsystem the org may not want (the
trial/coupon/referral "claim once" subsystem is being actively
deprecated per `docs/NORTH_STAR.md`'s MVP refactor — this faucet is
judged different in kind: testnet infrastructure for the token
initiative, not a monetization-discount mechanism).

There is also no wallet-to-account linkage in this backend — Epic 2
(wallet-based auth, gatewayz-backend#2248-2254) hasn't been built. This
design proves wallet ownership at claim time via a signed message
instead of relying on a stored link.

## Non-goals (explicitly out of scope for this design)

- A real Genesis Points ledger. Eligibility is a usage-count proxy,
  human-approved as sufficient for testnet.
- Wallet-to-account linkage / persistent wallet identity — that's Epic 2.
  This faucet only needs to verify wallet control for the duration of one
  claim request.
- An on-chain faucet contract — per human decision, minting is
  backend-signed via a `MINTER_ROLE` key the backend holds directly.
- Multi-chain support — one faucet, one contract, one chain
  (Avalanche Fuji), matching the rest of this initiative.
- Reclaim / top-up flows, admin override, or a claim-history UI — a
  single one-time claim per account and per wallet is the whole feature.

## Architecture

### Flow

1. `POST /faucet/nonce` (authenticated via `Depends(get_user_id)`, matching
   `src/routes/user_provider_keys.py`'s convention) — body: `{wallet_address}`.
   Generates a random nonce, stores it in Redis
   (`faucet_nonce:{user_id}:{wallet_address_lowercased}` → nonce, TTL 5
   minutes) via the same `_get_redis_client()` accessor pattern used in
   `src/services/email_verification.py`. Returns the exact message string
   the client must sign:
   `"Claim testnet WAYZ for Gatewayz account {user_id}. Nonce: {nonce}."`
2. Client signs that exact string with their wallet (`personal_sign` /
   EIP-191 — the standard "sign this message" flow every wallet supports,
   no typed-data/EIP-712 complexity needed for a single string).
3. `POST /faucet/claim` (authenticated, same dependency) — body:
   `{wallet_address, signature}`.
   - Look up the nonce for `(user_id, wallet_address)` in Redis; 400 if
     expired/missing (client must re-request a nonce).
   - Reconstruct the exact message string from step 1, recover the
     signer via `eth_account.Account.recover_message(encode_defunct(text=message),
     signature=signature)`; 401 if the recovered address doesn't match
     `wallet_address` (case-insensitive compare, both checksummed).
   - Delete the nonce (one-time use) regardless of outcome past this
     point.
   - Check eligibility: `usage_records` has ≥1 row for this `user_id`
     (`src/db/faucet.py::has_completed_at_least_one_request`). 403 if not.
   - Check `faucet_claims` for an existing row with this `user_id` OR
     this `wallet_address` (both columns UNIQUE — see Data model). 409 if
     either already claimed.
   - Insert a `faucet_claims` row with `status='pending'` BEFORE minting
     (claims the uniqueness slot atomically via the DB constraint, so a
     concurrent duplicate request fails at the DB insert rather than
     racing on-chain).
   - Mint via `WayzTokenFaucetClient.mint(wallet_address, CLAIM_AMOUNT)`
     (see below). On success, update the row to `status='sent'` with the
     `tx_hash`. On failure, update to `status='failed'` with the error
     and return 502 — the row stays (still counts toward the
     one-claim-per-user/wallet limit; a failed mint is not silently
     retryable by re-hitting the endpoint, since that could double-spend
     if the failure was actually a slow-confirming success. A stuck
     `failed` row is a manual/ops fix, not an automatic retry — this
     matches "no reclaim flow" in Non-goals).
   - Response: `{"success": true, "tx_hash": "0x...", "amount": "1000"}`.

### `WayzTokenFaucetClient` (new, separate from the indexer's read-only client)

A different trust tier from `WayzStakingClient` (Task 2 of the indexer
plan) — this one holds a live signing key, so it lives in its own module,
`src/services/chain/wayz_token_faucet_client.py`, not bolted onto the
read-only client.

- Holds an `eth_account.signers.local.LocalAccount` built from
  `Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY` (a plain env var — this matches
  how other system-level secrets are handled in this repo, e.g.
  `ENCRYPTION_KEY`/`ADMIN_API_KEY` in `src/config/config.py`, NOT the
  per-user Fernet keyring in `src/utils/crypto.py`, which is for
  user-owned BYOK keys, a different concern).
- `mint(to_address: str, amount_wayz: int) -> str` (returns tx hash):
  builds a `WAYZToken.mint(to, amount * 10**18)` transaction, signs it
  with the `LocalAccount`, sends it via `w3.eth.send_raw_transaction`,
  and returns the tx hash immediately — does NOT block waiting for
  confirmation (Fuji block time ~2s, but no reason to hold the HTTP
  response open on network latency; the claim row's `tx_hash` is enough
  for the client to check status via a block explorer, and the indexer's
  existing sync job will pick up the resulting stake once/if the user
  stakes it).
  - A module-level `asyncio.Lock` serializes calls to `mint()` — the
    account's on-chain transaction nonce would race under concurrent
    claims otherwise (two simultaneous claims reading the same
    `pending` nonce and one failing/replacing the other). Testnet claim
    volume doesn't need real concurrency here; correctness beats
    throughput.
  - Uses a NEW vendored ABI fragment,
    `src/services/chain/abi/wayz_token.json` (just `mint(address,uint256)`
    — separate from the indexer's `wayz_staking.json`, since this talks
    to `WAYZToken`, not `WAYZStaking`).

### Data model

New table `faucet_claims` (Supabase migration,
`YYYYMMDDHHMMSS_wayz_faucet_claims.sql`, idempotent/RLS-enabled
following existing conventions):

| column | type | notes |
|---|---|---|
| `id` | `bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY` | |
| `user_id` | `bigint NOT NULL UNIQUE` | one claim per account |
| `wallet_address` | `text NOT NULL UNIQUE` | lowercased; one claim per wallet |
| `amount` | `numeric(78, 0) NOT NULL` | raw wei-equivalent, matches `wallet_stakes`'s precision convention |
| `status` | `text NOT NULL CHECK (status IN ('pending','sent','failed'))` | |
| `tx_hash` | `text` | null until `status='sent'` |
| `error` | `text` | populated only if `status='failed'` |
| `claimed_at` | `timestamptz NOT NULL DEFAULT now()` | |

No RLS policy (service-role only), matching `wallet_stakes`/
`chain_sync_cursors`'s pattern — nothing reads this table from a
per-user request path in this design.

### Config additions (`_get_env_var` pattern, same section style as the
indexer's additions)

```python
WAYZ_TOKEN_CONTRACT_ADDRESS = _get_env_var("WAYZ_TOKEN_CONTRACT_ADDRESS")
WAYZ_FAUCET_MINTER_PRIVATE_KEY = _get_env_var("WAYZ_FAUCET_MINTER_PRIVATE_KEY")
WAYZ_FAUCET_CLAIM_AMOUNT = int(_get_env_var("WAYZ_FAUCET_CLAIM_AMOUNT", "1000"))
WAYZ_FAUCET_MIN_REQUESTS = int(_get_env_var("WAYZ_FAUCET_MIN_REQUESTS", "1"))
```

Both `WAYZ_TOKEN_CONTRACT_ADDRESS` and `WAYZ_FAUCET_MINTER_PRIVATE_KEY`
unset (true today — nothing is deployed to Fuji yet) means the claim
endpoint returns 503 ("faucet not configured") rather than crashing —
same no-op-until-deployed posture as the indexer.

### Rate limiting

Both endpoints get `create_endpoint_rate_limit("faucet_nonce", 10, 60)`
and `create_endpoint_rate_limit("faucet_claim", 5, 60)` (matching the
`src/services/endpoint_rate_limiter.py` factory pattern) — a coarse abuse
guard on top of the DB-level one-claim-per-user/wallet uniqueness, which
is the actual anti-sybil mechanism the issue asks for.

## Error handling / edge cases

- **Nonce reuse**: deleted immediately after the signature-recovery step
  regardless of success/failure, so a captured signature can't be replayed
  against a second claim attempt (a new nonce/message is required every
  time).
- **Signature from the wrong wallet**: recovered address doesn't match
  the claimed `wallet_address` → 401, no claim row created, nonce still
  consumed (client must request a fresh nonce and retry with the correct
  wallet).
- **Concurrent duplicate claims for the same user or wallet**: the
  `faucet_claims` table's `UNIQUE` constraints reject the second insert
  before either mint call happens — the DB is the actual race-safety
  mechanism, not application logic.
- **Mint transaction fails on-chain** (e.g. issuance cap hit — faucet
  mints share `WAYZToken`'s global 2%/yr cap with any other minting
  activity): claim row becomes `status='failed'`, endpoint returns 502.
  Since the row already exists (unique on `user_id`/`wallet_address`),
  that user/wallet cannot claim again without a manual DB fix — documented
  as an accepted limitation for testnet (Non-goals: no reclaim flow).
- **Faucet not configured** (`WAYZ_TOKEN_CONTRACT_ADDRESS` or
  `WAYZ_FAUCET_MINTER_PRIVATE_KEY` unset): 503 on `/faucet/claim`, before
  touching the DB or Redis.

## Testing

- `src/db/faucet.py`: mocked-Supabase unit tests, matching
  `tests/db/test_wallet_stakes.py`'s style — eligibility check, claim-row
  insert/update, uniqueness-violation handling.
- `src/services/chain/wayz_token_faucet_client.py`: mocked-web3 unit
  tests, matching `tests/services/chain/test_wayz_staking_client.py`'s
  style, PLUS a real (unmocked) signature round-trip test — sign a known
  message with a real `eth_account` test key, verify
  `Account.recover_message` recovers the same address. (This is the
  security-critical path; per the indexer work's lesson, a mocked
  `get_logs`/`recover_message` call proves nothing about correct usage —
  test it for real.)
- `src/routes/faucet.py`: `TestClient(app)` with
  `app.dependency_overrides[get_user_id]`, matching
  `tests/routes/test_admin_model_usage_analytics.py`'s pattern — nonce
  issuance, successful claim (mocked DB + mocked mint client), duplicate
  claim rejected, invalid signature rejected, unconfigured-faucet 503.

## Open questions for later (not blocking this spec)

- Whether `/faucet/claim`'s mint should eventually wait for on-chain
  confirmation before responding, once real usage patterns are known.
- Whether the eligibility bar (≥1 request) needs raising once testnet
  claim volume is observed.
