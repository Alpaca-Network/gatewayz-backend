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

## The jobs

| Job | Cadence | Module |
|---|---|---|
| `holdings_snapshots` | `HOLDINGS_SNAPSHOTS_PER_DAY` times a day (default 4, at 00:05, 06:05, 12:05, 18:05 UTC) | `src/services/holdings/snapshots.py` |
| `holdings_rewards` | once a day, 00:40 UTC, paying for yesterday | `src/services/holdings/rewards.py` |
| `holdings_sweep_watchdog` | hourly, 30 min after the sweep minute | `src/services/holdings/alerts.py` |

All three appear in `/admin/status` and `/admin/wayz/status` job health. They
always start and record a `skipped: disabled` run while the feature is off, so they
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

The flip side is that one unreadable chain skips **every** wallet. On
2026-10-08 prod had recorded zero wallets for three days because
polygon-rpc.com started answering 401. The RPC fallback and the sweep alerts
below exist so that cannot happen silently again.

### Supported tokens

The registry is ops-controlled (`/admin/holdings/tokens`), so the live set is
whatever `GET /admin/holdings/tokens` returns. Migrations add only rows that
were verified on-chain; they never touch a row ops already created.

| Chain | Token | Contract | Decimals | `price_id` |
|---|---|---|---|---|
| Ethereum (1) | stETH | `0xae7ab96520de3a18e5e111b5eaab095312d7fe84` | 18 | `staked-ether` |
| Ethereum (1) | wstETH | `0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0` | 18 | `wrapped-steth` |
| Ethereum (1) | rETH | `0xae78736cd615f374d3085123a210448e74fc6393` | 18 | `rocket-pool-eth` |
| Ethereum (1) | cbETH | `0xbe9895146f7af43049ca1c1ae358b0541ea49704` | 18 | `coinbase-wrapped-staked-eth` |
| Base (8453) | wstETH | `0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452` | 18 | `wrapped-steth` |
| Base (8453) | rETH | `0xb6fe221fe9eef5aba221c348ba20a1bf5e73624c` | 18 | `rocket-pool-eth` |
| Base (8453) | cbETH | `0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22` | 18 | `coinbase-wrapped-staked-eth` |
| Arbitrum One (42161) | wstETH | `0x5979d7b546e38e414f7e9822514be443a4800529` | 18 | `wrapped-steth` |
| Arbitrum One (42161) | rETH | `0xec70dcb4a1efa46b8f2d97c310c9c4790ba5ffa8` | 18 | `rocket-pool-eth` |
| Arbitrum One (42161) | cbETH | `0x1debd73e752beaf79865fd6446b0c970eae7732f` | 18 | `coinbase-wrapped-staked-eth` |

These come from `supabase/migrations/20261008120000_holdings_tokens_liquid_eth.sql`,
which records how each address was verified. **Each token has its own price
id, never ETH's.** wstETH, rETH and cbETH do not rebase, and each is worth more
than one ETH by a growing ratio, so valuing them as ETH underpays every holder.
stETH rebases, and `balanceOf` already includes the rebase.

### RPC endpoints and fallback

Each chain resolves its endpoints in `src/services/holdings/chains.py`
(`rpc_endpoints_for_chain`), in this order:

1. `<CHAIN>_RPC_URL` (`ETHEREUM_`, `BNB_CHAIN_`, `POLYGON_`, `BASE_`,
   `ARBITRUM_`, `AVALANCHE_`), **if it differs from the public default**. A
   value equal to the default is treated as unset.
2. Alchemy, if `ALCHEMY_API_KEY` is set: `https://{net}.g.alchemy.com/v2/{key}`
   with `eth-mainnet`, `bnb-mainnet`, `polygon-mainnet`, `base-mainnet`,
   `arb-mainnet` or `avax-mainnet`.
3. The public default, then a secondary public endpoint run by a different
   operator.

The first entry is the primary. When it fails on **transport** (connection
error, timeout, an HTTP error such as 401/429/5xx, a non-JSON-RPC response, or
a JSON-RPC rate-limit code -32005/-32016/429), the whole chain is re-read on the
next endpoint before it counts as failed. The endpoint that failed then goes to
the back of the list for 5 minutes. It is only moved back, never dropped.
**A contract revert never falls over.** A revert comes from the call itself
and would happen on every endpoint, and at the raw JSON-RPC layer it looks the
same as a provider error. So any JSON-RPC error without a known rate-limit
code is treated as not-transport.

The key never reaches a log. requests puts the request URL, and so the key,
in its exception messages. So an RPC error is never logged or stored as
`str(exc)`. `describe_rpc_error` reduces it to the exception class, the HTTP
status and the JSON-RPC code, and that is all that reaches a log, a
`ChainReadFailure.reason`, a sweep summary or an alert. web3 and urllib3 log
the endpoint URL at DEBUG, so their loggers carry a filter that masks URL
paths, `/v2/<key>` segments and `ALCHEMY_API_KEY`.
`tests/services/holdings/test_secret_hygiene.py` runs a full sweep and its
alert against errors that contain a fake key, and checks the key appears in
none of the logs, the summary, the failure reasons or the alert email.
`/admin/status` reports only whether the key is present.

### Sweep alerts

Both alerts are in `src/services/holdings/alerts.py`:

- **Recorded nothing.** Fires right after a sweep that considered eligible
  wallets (`wallets_considered > 0`) but recorded none
  (`sweeps_recorded == 0`). The alert gives the skip counts and the failed
  chains (`summary["failed_chains"]`, chain id to the number of wallets it
  failed for).
- **Stale.** An hourly watchdog job, `holdings_sweep_watchdog`, fires when no
  sweep has recorded any wallet for `HOLDINGS_SWEEP_STALE_HOURS` (default 8)
  while wallets old enough to be considered exist. This catches the case where
  the sweep is not running at all. If the registry has never recorded a
  wallet, the clock starts when the instance first checks, so enabling the
  feature does not alert before its first sweep.

Each condition alerts at most once per `HOLDINGS_ALERT_COOLDOWN_HOURS`
(default 12). The claim is a Redis key `ops:alert:holdings:{condition}` set
with `SET NX EX`, so several instances send one email between them. When
Redis is down, an in-process fallback takes its place. If delivery fails, the
claim is released so the next occurrence can retry.

Delivery uses the provider-alert path: `OPS_ALERT_EMAIL` first, then the
active admin/superadmin staff emails, sent through Resend. Every alert is also
logged at ERROR, which Sentry captures. With no recipient at all, that ERROR
plus one "has no recipient: set OPS_ALERT_EMAIL" ERROR is all that happens.
Alerts carry counts, chain ids and timestamps only. They never include a wallet
address, an RPC URL or a key.

`GET /admin/status` has a `holdings_sweeps` block: `stale`,
`last_recorded_at`, `hours_since_last_recorded`, `eligible_wallets`, and
`last_sweep` (considered, recorded, skipped, failed_chains,
`recorded_nothing`), plus a single `degraded` flag. The `holdings_snapshots`
job can read `ok` while skipping every wallet. This block shows when that
happens.

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
4. Cap at the account's **usage allowance** — what it actually spent on
   inference over the last `HOLDINGS_USAGE_LOOKBACK_DAYS` days (default 7)
   times `HOLDINGS_USAGE_MATCH_MULTIPLIER` (default 1.0), minus the holdings
   credits it has already been granted inside that same window. Holding alone
   earns nothing: the programme exists to turn holders into customers, and
   unlike staking, a holder's capital does no work for us. The allowance is a
   budget the window *consumes*, not a ceiling that resets nightly — applied
   per day, one week's spend could be claimed once a day for a week. An
   account with no spend is skipped as `no_usage`; a wallet with no account is
   skipped as `unlinked`, because a pending row would be paid in full the
   moment it links. Wallets clipped by this ceiling are counted in the run
   summary as `usage_capped`. Set `HOLDINGS_USAGE_MATCH_ENABLED=false` to pay
   on holdings alone.
5. Cap at `HOLDINGS_DAILY_CAP_CREDITS`, **per account** — an account's other
   wallets' accruals for that day are subtracted first, so splitting a balance
   across wallets cannot multiply the ceiling. A wallet not yet linked to an
   account has none to charge against and is capped on its own.
6. Cap at `HOLDINGS_GLOBAL_DAILY_BUDGET_CREDITS` across the run. Wallets are
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
7. Insert the accrual **pending**, then grant via `add_credits_to_user(...,
   transaction_type="holdings_reward",
   request_id="holdings_reward:{wallet}:{date}")`, then mark it paid.

Idempotent twice over, exactly like staking rewards: `holdings_reward_accruals`
is `UNIQUE (wallet_address, reward_date)` and is written before any credit is
granted, and `credit_transactions.request_id` has its own partial unique index.
With the usage match off, a wallet that is not linked accrues pending and is
paid on link; a bounded 30-day sweep retries anything still pending from
earlier days.

A reward date with no observations at all raises
`HoldingsSnapshotsMissingError`, recorded as a **failed** job run and returned
as a 409 from the manual-run endpoint. It means the sweep did not run, which an
operator has to see rather than read as an empty but healthy payout.

### Reading a run summary

`summary["skipped"]` breaks every declined wallet out by reason and never sums
them: `no_snapshots`, `too_few_batches`, `no_rate_tier`, `zero_credits`,
`unlinked`, `no_usage`, `budget_exhausted`. When a run pays less than expected,
which reason grew is the diagnosis — a jump in `no_usage` means holders are not
running inference, which is the programme working rather than failing.
`skipped_total` is the sum, `budget_skipped` repeats the budget count because it
is the ceiling operators watch directly, `usage_capped` counts wallets clipped
to their spend, and a whole-run no-op reports `{"skipped": "disabled"}` instead
of the dict.

The sweep's own summary is broken out the same way: `too_new`, `unknown_age`,
`incomplete_read`, `missing_price`, `sweep_write_failed`, `error`, alongside
`sweeps_recorded` and `failed_chains` (chain id to the number of wallets whose
read failed on it). A rise in `incomplete_read` or
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

1. Seed the registry: `POST /admin/holdings/tokens` per asset. The original
   migration seeds nothing, and an empty registry means the sweep no-ops with
   `skipped: no_tokens`. The liquid ETH tokens above are added by their own
   migration.
2. Set `ALCHEMY_API_KEY`, or point the six `*_RPC_URL` vars at a paid
   provider. Public endpoints rate-limit, and a chain that fails on every
   endpoint is a **failed** read, which drops the whole batch for that wallet.
   Set `OPS_ALERT_EMAIL` so the sweep alerts reach someone.
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
