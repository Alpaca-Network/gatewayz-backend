# Delegated staking (inference-as-yield v2)

Users stake from **their own wallet** to Gatewayz-run staking infrastructure, and
we grant them an **inference allowance** in credits. Non-custodial: the stake
never leaves the user's control and we never take a deposit.

| Asset | Where users stake | How the rewards reach us |
|---|---|---|
| ETH | Gatewayz StakeWise V3 vault (`STAKEWISE_VAULT_ADDRESS`) | Vault fee (`feePercent`, set near the max), minted as vault shares to `feeRecipient` |
| ADA | Gatewayz Cardano pool (`CARDANO_POOL_ID`) | Pool margin (near 100%) + fixed cost = Koios `pool_history.pool_fees` |

## Economics

If **R** = staking revenue to us per USD staked per day and **m** = our inference
gross margin, a credit costs us `(1 - m)` dollars, so the break-even allowance is
**A = R / (1 - m)** credits per USD per day. The rate actually paid is whatever an
admin sets in `delegation_allowance_rates` (per asset, credits per $1k per day).
`GET /admin/delegation/rates` shows `suggested = 1000 × R / (1 - m)` from
`DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_{ETH,ADA}` — it is never applied
automatically. The user-facing framing is "99% off up to $X/month", where
`X = /delegation/rewards → allowance.month_estimate_usd` (30 × the daily estimate,
1 credit = $1).

## Legal guardrail

This is **not** a fixed or guaranteed yield (SEC/CFTC Release 33-11412). Rates
are admin-set, can change at any time, and are published as the *current* rate.
Every public response carries:

> Rates are set by Gatewayz, can change at any time, and are not a guaranteed return.

Never describe the allowance as APY/APR, interest, or a return on the user's
stake. `tests/routes/test_delegation.py::TestGuardrail` greps the route module.

## Flow

1. **Link a wallet.**
   - ETH: the existing SIWE link (`/auth/wallet/link/nonce` → `/auth/wallet/link`).
     Any linked EVM wallet is read against the vault on `STAKEWISE_VAULT_CHAIN_ID`.
   - ADA: `POST /auth/wallet/cardano/nonce {stake_address}` →
     `{nonce, message, payload_hex, expires_at}`; the wallet calls CIP-30
     `signData(stake_address, payload_hex)`; `POST /auth/wallet/cardano/link
     {stake_address, signature, key}`. Verified in `src/services/delegation/cip8.py`:
     key-hash reward address (`stake1…`, testnet only with
     `DELEGATION_ALLOW_CARDANO_TESTNET`), EdDSA COSE_Sign1 whose `address` header
     is the stake address, payload == the issued message (or its blake2b-224 when
     `hashed`), Ed25519 signature over `["Signature1", protected, h'', payload]`,
     and blake2b-224(public key) == the address's stake credential. Nonce is
     single-use (`GETDEL`), 5-minute TTL. Stored in `user_wallets` with
     `chain_namespace='cip34'`, `source='cip30'`, never primary (it cannot sign in).
     Script stake addresses (`stake17…`) cannot sign and are rejected.
2. **Measure** (`delegation_measurements`, `DELEGATION_MEASUREMENTS_PER_DAY`
   sweeps/day at :`DELEGATION_MEASUREMENT_CRON_MINUTE_UTC`). ETH:
   `getShares(wallet)` → `convertToAssets(shares)` → USD (CoinGecko `ethereum`).
   ADA: Koios `pool_delegators` (all pages) matched to linked stake addresses,
   counted only once `active_epoch_no <= tip epoch`, lovelace → USD (`cardano`).
   **A measured zero is a row; a failed read or missing price writes nothing.**
3. **Reconcile** (daily, 00:30 UTC) — see below.
4. **Accrue** (daily, 00:50 UTC, for yesterday): basis = the day's **lowest**
   measurement; a day needs ≥ `DELEGATION_MIN_MEASUREMENTS_PER_DAY`
   measurements. `credits = basis / 1000 × rate`, capped per account per day
   (`DELEGATION_DAILY_CAP_CREDITS`, all wallets and assets together) and by a
   global daily budget (`DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS`, rotating
   order). **Cap and budget are enforced atomically in SQL**:
   `delegation_reserve_accrual` caps the credits at the account's remaining
   headroom (decision-time account, paid account and currently linked wallets,
   all assets), refuses past the global budget or on a paused asset, and inserts
   the `pending` row — all under one per-date `pg_advisory_xact_lock`. Paying
   goes through `delegation_claim_accrual`, which re-checks pause and cap under
   the same lock (`pending → claimed`, or `void` if it no longer fits) before any
   credit moves. No cap/budget decision is read-then-write in Python.
   Written `pending` first, then `add_credits_to_user(transaction_type=
   "delegation_reward", request_id=uuid5("delegation:{asset}:{wallet}:{date}"))`
   (the ledger column is UUID-typed; the readable key is in the metadata as
   `grant_key`), then marked paid. Pending rows are retried for 30 days and paid
   on link; the account cap is re-checked at payment time (counting paid rows)
   and a pending row that no longer fits is voided.

## Fail-closed rules

- `DELEGATED_STAKING_ENABLED=false` (default): every job records
  `{"skipped": "disabled"}`, endpoints report `enabled: false`, no rates published.
- Vault / pool unset: that asset's readers make **no external call**, its rate
  cannot be activated (`PUT /admin/delegation/rates` → 422), and it accrues nothing.
- Rates are seeded at 0 and **inactive**.
- Reconciliation: `cost = credits granted (pending + paid) × (1 - m)`. If
  `cost > revenue × (1 + DELEGATION_RECONCILIATION_TOLERANCE) +
  DELEGATION_RECONCILIATION_GRACE_USD`, the asset is **paused**
  (`delegation_controls.accruals_paused`) and ops is alerted. A paused asset gets
  no new accrual and no pending payout. Only `POST /admin/delegation/resume
  {asset}` (superadmin, audited) un-pauses it; the next run re-pauses if the
  overspend persists.
- The pause fails closed: an unreadable controls table, a missing row, or a row
  without an explicit `accruals_paused = false` is **paused** — in Python and in
  the SQL reserve/claim functions. A failed reserve/claim (cap or budget read)
  reserves and pays nothing. An unreadable sum is reported `unknown` (never zero).
- Authorization fails closed: credits only go to the account the wallet is
  linked to **right now**. The SQL claim refuses (`not_linked`) unless the
  wallet is linked to the payee; immediately before the credit write the link is
  re-verified, and anything else (unlinked, inactive, moved to another account,
  lookup error, missing/malformed user id) releases the claim back to `pending`
  without paying.
- ETH revenue is the day-over-day growth of `getShares(feeRecipient)`. The first
  reading — and the first after `feeRecipient` changes — is a zero baseline;
  shares moved out count as zero, never negative. ADA revenue is recorded once
  per epoch, only for epochs ≤ tip − 2 (when rewards are final).

## Env vars (Railway service `api`)

| Var | Default | Notes |
|---|---|---|
| `DELEGATED_STAKING_ENABLED` | `false` | Master switch |
| `STAKEWISE_VAULT_ADDRESS` | unset | Vault contract; unset → ETH off |
| `STAKEWISE_VAULT_CHAIN_ID` | `1` | Uses the holdings RPC config/failover for that chain |
| `CARDANO_POOL_ID` | unset | bech32 `pool1…`; unset → ADA off |
| `KOIOS_BASE_URL` | `https://api.koios.rest/api/v1` | |
| `KOIOS_API_KEY` | unset | Secret; sent as a Bearer header only, in the secrets registry |
| `DELEGATION_ALLOW_CARDANO_TESTNET` | `false` | Never on in prod |
| `DELEGATION_INFERENCE_MARGIN` | `0.20` | m |
| `DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ETH` / `_ADA` | `0` | R, suggestion only |
| `DELEGATION_DAILY_CAP_CREDITS` | `5` | Per account per day |
| `DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS` | `50` | All accounts per day |
| `DELEGATION_MEASUREMENTS_PER_DAY` / `DELEGATION_MIN_MEASUREMENTS_PER_DAY` | `4` / `2` | |
| `DELEGATION_RECONCILIATION_TOLERANCE` | `0.05` | |
| `DELEGATION_RECONCILIATION_GRACE_USD` | `0` | Absolute allowance for revenue lag |
| `DELEGATION_*_CRON_*` | see `src/config/config.py` | |

## What must exist on-chain before enabling

1. **StakeWise V3 vault** deployed with `feePercent` set **at creation** to the
   target (max `10_000` bps = 100%). After creation the fee can only rise ×1.2
   per 3 days, and from 0 only to 1% — so a vault created at a low fee takes
   weeks to reach ~99%. `feeRecipient` = a Gatewayz address whose vault shares
   are **never moved** (moving them reads as zero revenue and can pause ETH).
   The vault must have validators registered and be harvesting (fees are only
   minted on harvest).
2. **Cardano pool** registered and producing blocks, margin near 100%, pledge
   met. Rewards land ~2 epochs (~10 days) after the stake snapshot, so set
   `DELEGATION_RECONCILIATION_GRACE_USD` to cover ~3 epochs of grants at launch
   or ADA will pause on day one.
3. Then: set the rate (`PUT /admin/delegation/rates`, superadmin) → set the env
   vars → `DELEGATED_STAKING_ENABLED=true` → `POST /admin/delegation/run
   {"job":"measure"}` and check row counts in `/admin/status.delegation`.

## Endpoints

`GET /delegation/status` (public) · `GET /delegation/rewards` (user) ·
`GET|PUT /admin/delegation/rates` · `POST /admin/delegation/run {job: measure|accrue|reconcile, reward_date?}` ·
`GET /admin/delegation/reconciliation` · `POST /admin/delegation/resume {asset}` ·
`POST /auth/wallet/cardano/nonce` · `POST /auth/wallet/cardano/link`.
Mutations are superadmin-only and audited. `/admin/status` has a `delegation` block.

## Known residuals

- Reproduce the SQL concurrency check with
  `scripts/checks/delegation_reserve_concurrency.sh` (scratch Postgres).
- Cardano rewards come from the stake snapshot two epochs earlier; credits are
  paid on the day's measured live stake, so day-level credits and revenue are
  not aligned — reconciliation is cumulative for that reason.
- The accrual's per-account cap reads the account's *current* wallets; a wallet
  unlinked mid-day is capped on its own.
