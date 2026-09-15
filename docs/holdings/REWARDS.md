# Holdings rewards

Inference credits for tokens a user holds in a wallet they have proven they
control.

**What this is not.** We take no custody and accept no deposit; we only read
public balances over RPC. The credits are a usage grant this platform funds,
not a return on anyone's capital, and the rate can change or stop at any time.
Nothing here is a staking product and nothing here promises a yield. Keep that
language out of the code and the API: `tests/routes/test_holdings.py` fails the
build if it creeps back in.

Ships dark. Everything below is inert until `HOLDINGS_REWARDS_ENABLED=true`.

## The two jobs

| Job | Cadence | Module |
|---|---|---|
| `holdings_snapshots` | `HOLDINGS_SNAPSHOTS_PER_DAY` times a day (default 4, at 00:05, 06:05, 12:05, 18:05 UTC) | `src/services/holdings/snapshots.py` |
| `holdings_rewards` | once a day, 00:40 UTC, paying for yesterday | `src/services/holdings/rewards.py` |

Both appear in `/admin/status` and `/admin/wayz/status` job health. Both always
start and record a `skipped: disabled` run while the feature is off, so they
read as off rather than missing.

### The sweep

For every wallet in `user_wallets` at least `HOLDINGS_MIN_WALLET_AGE_DAYS` old,
read its balance of every enabled `holdings_tokens` row across six EVM chains
and price it in USD. A fully measured wallet gets:

- exactly one `wallet_holdings_sweeps` row, that sweep's total USD value,
  **written even when the total is zero**; and
- one `wallet_holdings_snapshots` row per token actually held, the per-token
  detail behind that total.

All rows of one sweep share one `taken_at`, which joins the detail to its sweep.

**The zero row is load-bearing.** An empty wallet produces no per-token rows, so
if the sweep were inferred from those rows, a wallet emptied between sweeps
would leave no trace rather than being valued at zero. The day's minimum would
then be taken across only the sweeps in which it happened to be funded: fund a
wallet just before two of four daily sweeps, empty it the rest of the day, and
it gets paid as though it held that balance all day. Recording the zero is what
makes "paid on the lowest value seen that day" true. `wallet_holdings_snapshots`
is audit and user-facing detail only; the payout never reads it.

Two rules exist to avoid recording wrong data, not to save money, and both
suppress the sweep row as well as the detail. **A wallet whose chain read was
incomplete records nothing, and a wallet holding a token with no fresh price
records nothing.** These are cases where we do not know the total, and "we could
not measure it" must never be written down as "they held zero" — an RPC outage
would otherwise zero out every holder's day. A token the wallet holds zero of
blocks nothing. Skips are logged with a reason and counted in the run summary.

### The accrual

For each wallet observed on the reward date:

1. The day must have at least `HOLDINGS_MIN_SNAPSHOT_BATCHES_PER_DAY` completed
   sweeps in `wallet_holdings_sweeps`
   (default 2, clamped to `HOLDINGS_SNAPSHOTS_PER_DAY`). "Lowest of the day"
   only resists farming when the day has several readings; with one recorded
   sweep the minimum is just that moment, which is the hole the rule exists to
   close. A wallet can legitimately end a day with one sweep, because the sweep
   drops a whole batch on an incomplete chain read or a missing price, so this
   is a real case. Fewer than the required number and the day is skipped as
   `too_few_batches` — underpaying nobody is fine, paying on one farmable
   reading is not.
2. basis = the day's **lowest** sweep total, zero rows included. Not an
   average, not the latest. A wallet funded for ten minutes is worth its empty
   total.
3. credits = basis / 1000 x the matching tier's `credits_per_1k_usd_per_day`,
   rounded down at 6 dp.
4. Cap at `HOLDINGS_DAILY_CAP_CREDITS`, **per account** — an account's other
   wallets' accruals for that day are subtracted first, so splitting a balance
   across wallets cannot multiply the ceiling. A wallet not yet linked to an
   account has none to charge against and is capped on its own.
5. Cap at `HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS` across the run. Wallets are
   considered in an order seeded by a hash of (reward date, address): the same
   order every time that date is processed, so a re-run pays exactly the same
   wallets, but a different order tomorrow, so no wallet is permanently
   advantaged by its address on the days the budget runs out. Accruals that
   already exist for the date consume budget first, so re-running one date
   cannot spend a second budget. Once a wallet does not fit, granting stops
   entirely rather than letting smaller wallets leapfrog it; the rest are
   reported as `budget_skipped`. Each wallet is still evaluated on its own
   merits first, so one that would have been skipped anyway is counted under
   its own reason rather than inflating the budget count.
6. Insert the accrual **pending**, then grant via `add_credits_to_user(...,
   transaction_type="holdings_reward",
   request_id="holdings_reward:{wallet}:{date}")`, then mark it paid.

Idempotent twice over, exactly like staking rewards: `holdings_reward_accruals`
is `UNIQUE (wallet_address, reward_date)` and is written before any credit is
granted, and `credit_transactions.request_id` has its own partial unique index.
A wallet that is not linked accrues pending and is paid on link; a bounded
30-day sweep retries anything still pending from earlier days.

A reward date with no observations at all raises
`HoldingsSnapshotsMissingError`, recorded as a **failed** job run and returned
as a 409 from the manual-run endpoint. It means the sweep did not run, which an
operator has to see rather than read as an empty but healthy payout.

### Reading a run summary

`summary["skipped"]` breaks every declined wallet out by reason and never sums
them: `no_snapshots`, `too_few_batches`, `no_rate_tier`, `zero_credits`,
`budget_exhausted`. When a run pays less than expected, which reason grew is
the diagnosis. `skipped_total` is the sum, `budget_skipped` repeats the budget
count because it is the ceiling operators watch directly, and a
whole-run no-op reports `{"skipped": "disabled"}` instead of the dict.

The sweep's own summary is broken out the same way: `too_new`, `unknown_age`,
`incomplete_read`, `missing_price`, `sweep_write_failed`, `error`, alongside
`sweeps_recorded`. A rise in `incomplete_read` or
`missing_price` there shows up as a rise in `too_few_batches` here a day later.

## API

| Endpoint | Auth |
|---|---|
| `GET /holdings/rewards` | the caller |
| `GET /admin/holdings/rates` | admin or env key |
| `PUT /admin/holdings/rates` | superadmin, audited |
| `GET /admin/holdings/tokens` | admin or env key |
| `POST /admin/holdings/tokens` | superadmin, audited |
| `PATCH /admin/holdings/tokens/{id}` | superadmin, audited |
| `POST /admin/holdings/rewards/run` | superadmin, audited |
| `GET /admin/holdings/rewards/summary` | admin or env key |

`PUT /admin/holdings/rates` requires a `min_usd = 0` tier and unique floors:
without a zero tier, a wallet below every floor matches no tier and is silently
skipped instead of earning the base rate. The rate table is never mutated in
place -- a change deactivates the old set and inserts a new one -- because a
tier an accrual was computed against must not shift under it.

The per-wallet value in `GET /holdings/rewards` is the **latest** observation,
while the payout uses the day's **lowest**, so a wallet whose balance moved
during the day earns less than the estimate shown. `estimated_credits_per_day`
is capped at the daily ceiling (what would actually be paid);
`uncapped_credits_per_day` sits beside it so a UI can show the cap biting.

## Going live

1. Seed the registry: `POST /admin/holdings/tokens` per asset. The migration
   deliberately seeds nothing; an empty registry means the sweep no-ops with
   `skipped: no_tokens`.
2. Point the six `*_RPC_URL` vars at a paid provider. Public endpoints
   rate-limit, and a rate-limited chain is a **failed** read, which drops the
   whole batch for that wallet.
3. Set the tiers: `PUT /admin/holdings/rates` with a superadmin key. The
   migration's placeholder pays 0.
4. Set `HOLDINGS_REWARDS_ENABLED=true` on Railway `api`.
5. Let a full day of sweeps run before the first accrual. A partial first day
   has too few batches and is skipped rather than paid on one reading.

## Gotchas

- `railway` CLI links are per-directory: run it from `gatewayz-backend/`
  proper, never from a worktree, or it silently targets another project.
- A promoted staged migration needs a **new** version stamp; a reused one kills
  `supabase db push` on `schema_migrations_pkey`.
