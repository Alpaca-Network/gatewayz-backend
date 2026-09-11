# Staking rewards (WAYZ stakers paid in inference credits)

**Rule (from the boss):** people who stake WAYZ are paid in **inference
credits** on Gatewayz — the normal credit balance every model call bills
against. Providers who offer inference are paid in **WAYZ** (already
built: M4 provider earnings + settlement; a separate, already-shipped
system, not this doc).

This complements `docs/api.md`'s "WAYZ Staking & Faucet" section, which
covers the read-only `/staking/wallets/{address}` and `/staking/summary`
endpoints backing `wallet_stakes`. This doc covers the reward layer built
on top: `staking_reward_rates`, `staking_reward_accruals`, the daily job,
and the admin controls.

## How it's computed

Once a day (00:20 UTC by default — `Config.STAKING_REWARDS_CRON_HOUR_UTC` /
`STAKING_REWARDS_CRON_MINUTE_UTC`), `src/services/staking_rewards.py::
run_staking_rewards_once` pays every wallet with `staked_amount > 0` in
`wallet_stakes` for **yesterday** (UTC):

1. Gate: `Config.STAKING_REWARDS_ENABLED` (default `false`). Off ⇒ the run
   records `{"skipped": "disabled"}` and does nothing else.
2. Stale guard: if `wallet_stakes` is empty, or the most recent
   `last_synced_at` is older than `2 × WAYZ_STAKING_SYNC_INTERVAL_MINUTES +
   30` minutes, the whole run raises `StakingRewardsStaleError` rather than
   pay based on stake data that might no longer be accurate.
3. For each staked wallet: `staked_wayz = staked_amount_wei / 1e18`
   (`Decimal`, never `float`). The **rate table**
   (`staking_reward_rates`) is a tiered "credits per 1000 WAYZ per day"
   schedule — the active row with the largest `min_stake_wayz <=
   staked_wayz` applies. `credits = staked_wayz / 1000 × rate`, quantized
   to 6 decimal places.
4. The result is capped at `Config.STAKING_REWARDS_DAILY_CAP_CREDITS`
   (default `50.0`) — the ledger transaction's metadata records
   `capped: true` when this happens.
5. If the (capped) result is below `Config.STAKING_REWARDS_MIN_CREDITS`
   (default `0.0001`), the day is recorded `skipped` with
   `skip_reason: "below_min"` and nothing is paid — this keeps
   dust-amount, effectively-zero rewards out of the ledger.
6. Otherwise one `staking_reward_accruals` row is written per
   `(wallet_address, reward_date)`, and if the wallet is linked to an
   active Gatewayz account, `src/db/users.py::add_credits_to_user` is
   called with `transaction_type="staking_reward"` — the normal credit
   ledger, not a side channel. The credit's `metadata` carries `wallet`,
   `reward_date`, `staked_amount_wei`, `rate_id`, `capped`.

## Idempotency

`staking_reward_accruals` has a `UNIQUE (wallet_address, reward_date)`
index — the job is safe to run any number of times for the same date.
Before doing any work for a wallet/date, the job checks for an existing
row:

- `paid` or `skipped` ⇒ already decided, nothing more happens.
- `pending` ⇒ retried (see below).
- no row ⇒ first time; a `pending` row is created, then paid.

Amounts are `Decimal` end-to-end; `float` only appears at the
`add_credits_to_user()` call boundary (a USD-float API).

## Unlinked wallets and pending accruals

A wallet that isn't linked to a Gatewayz account yet still accrues: the
row is written with `user_id = null` and `status = 'pending'`. Two things
can later pay it:

- **Linking the wallet.** `src/db/user_wallets.py::link_wallet`'s callers
  (Privy wallet ingest, SIWE signup, SIWE link — `src/routes/auth.py` and
  `src/routes/wallet_auth.py`) call
  `staking_rewards.pay_pending_for_wallet(address, user_id)` right after a
  successful link. This pays every pending accrual for that wallet from
  the last 30 days. The call is lazy-imported and never raises — a
  signup/login must never fail because of a staking-rewards side effect —
  and no-ops immediately if the feature is disabled.
- **The next scheduled run.** Independent of any specific wallet linking,
  every run also sweeps `pending` accruals from the last 30 days
  (`list_pending_accruals`) and retries them. This is also what recovers a
  **failed credit write**: if `add_credits_to_user` raises (a transient DB
  error, say), the row is left `pending` with `skip_reason` set to the
  exception's class name, and the next run tries again.

Accruals older than 30 days are never retried automatically — a stuck
`pending` row past that window needs manual attention (see Ops below).

## Rate table

Seeded with placeholder values on migration
(`supabase/migrations/20260911120000_staking_rewards.sql`):

| `min_stake_wayz` | `credits_per_1k_wayz_per_day` |
|---:|---:|
| 0 | 0.010000 |
| 10,000 | 0.012000 |
| 100,000 | 0.015000 |

These are intentionally tiny placeholders — real numbers are a product
decision the boss confirms before `STAKING_REWARDS_ENABLED` flips on in
production (see Rollout in the design spec). Changing the table
(`PUT /admin/staking/reward-rates`) never mutates existing rows in place —
it deactivates the old set and inserts a new one, so a rate already
referenced by a paid accrual (`staking_reward_accruals.rate_id`) is never
changed retroactively.

## API

- `GET /staking/rewards` (authenticated) — the calling user's rate table,
  linked wallets with a live daily-credit estimate, running totals
  (`credits_paid_30d`, `credits_paid_all`, `pending_credits`), and the last
  30 accrual rows. All numbers are strings.
- `GET /staking/wallets/{address}` (public, unchanged otherwise) now also
  returns a `rewards: {estimated_credits_per_day, rate_credits_per_1k}`
  block computed purely from the rate table — no user/wallet-link data, so
  it's safe on this no-auth endpoint.
- Admin (`src/routes/admin_staking.py`):
  - `GET /admin/staking/reward-rates` — admin API key or `ADMIN_API_KEY`.
  - `PUT /admin/staking/reward-rates` — superadmin only, audited
    (`staking.rates.update`).
  - `POST /admin/staking/rewards/run` — superadmin only, audited
    (`staking.rewards.run`). Runs the job right now for an optional
    `reward_date` (default yesterday UTC) — same idempotency guarantees as
    the scheduled run. A stale sync returns `409` with
    `error.context.parameter_value = "stake_sync_stale"`.
  - `GET /admin/staking/rewards/summary` — admin API key or
    `ADMIN_API_KEY`; the last job run (from the ops job registry) plus
    global totals across every user.

## Ops

- The job is registered in the ops job map
  (`src/routes/admin_wayz.py::_JOB_INTERVAL_MINUTES["staking_rewards"] =
  1440`) — `GET /admin/wayz/status` shows its last run, whether it's stale,
  and its summary the same way as every other scheduled job.
- A `pending` accrual older than 30 days needs manual investigation — check
  `staking_reward_accruals` for `status = 'pending'` and
  `reward_date < now() - interval '30 days'`. The most likely cause is a
  wallet that was never linked; the fix is linking it (which pays
  everything within the retry window) or, for anything past 30 days, a
  manual credit grant referencing the accrual row.
- Enabling in production: set `STAKING_REWARDS_ENABLED=true` on Railway's
  `api` service once the real rate table has been confirmed and applied
  via `PUT /admin/staking/reward-rates`. Smoke test: stake with a test
  wallet, run `POST /admin/staking/rewards/run` for yesterday, confirm the
  credit lands in Settings → Credits and the Earnings card on `/staking`.
