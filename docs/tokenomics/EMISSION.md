# WAYZ emission rewards (Chutes-style)

**Boss asks:** split WAYZ rewards between stakers and GPU providers the
way [Chutes](https://chutes.ai/docs/miner-resources/scoring) (a
[Bittensor](https://docs.learnbittensor.org/learn/emissions) subnet)
does. This doc is the binding design, a worked example, and the operator
tuning guide. See also `docs/gpu/VERIFICATION_AND_PAYOUTS.md` (per_unit
vs emission for providers) and `docs/staking/REWARDS.md` (rate-table vs
emission for stakers) -- both already-shipped systems this mode replaces
for the day it's live.

## Chutes/Bittensor facts this mirrors

Per tempo, a Bittensor subnet's emission splits **41% miners (providers),
41% validators+stakers, 18% subnet owner** (treasury). Miners are ranked
by a rolling **7-day score = 55% compute + 20% response speed + 20%
availability + 5% bounties** (Chutes' unique-chute/bounty count metric);
entries at or above the median get raised to the **1.3 exponent**.
Stakers earn pro-rata to stake. We mirror the split and the score formula
exactly; the "bounties" metric becomes **unique models served** (the
closest Gatewayz analogue -- there is no bounty/chute-uniqueness concept
here, but "serves more distinct models" is the same "breadth of useful
work" signal), and "validators" collapses to "stakers" (Gatewayz has no
separate validator role).

## Turning it on

`Config.REWARDS_MODE` (default `per_unit`) gates everything below --
**ships dark**. Until it's set to `emission`:

- `record_earning_for_verified_work` keeps accruing per-unit provider
  earnings exactly as before (`docs/gpu/VERIFICATION_AND_PAYOUTS.md`).
- `run_staking_rewards_once` keeps paying stakers off the rate table
  exactly as before (`docs/staking/REWARDS.md`).
- The `emission_epoch` job runs on schedule but immediately returns
  `{"skipped": "disabled"}` (recorded on the ops job page like any other
  run).

Flipping `REWARDS_MODE=emission` switches BOTH provider and staker
payouts to this design simultaneously -- there is no way to run "emission
for providers, rate-table for stakers" or vice versa, by design (the boss
asked for one consistent split, not two independently-tuned systems).

## The daily split

`WAYZ_DAILY_EMISSION` (Decimal WAYZ/day, default `100000` -- a testnet
placeholder, **not** a production number) is split by basis points:

| Leg | Config | Default |
|---|---|---:|
| Providers | `EMISSION_SPLIT_PROVIDERS_BPS` | 4100 (41%) |
| Stakers | `EMISSION_SPLIT_STAKERS_BPS` | 4100 (41%) |
| Treasury | `EMISSION_SPLIT_TREASURY_BPS` | 1800 (18%) |

Validated at startup (`src/services/emission/epoch.py::check_emission_config`,
called from `src/services/startup.py`'s lifespan): the three **must** sum
to 10000, and so must the four score weights below. A bad sum never
crashes boot -- it logs at `ERROR` and disables the `emission_epoch` job
(`run_emission_epoch` returns `{"skipped": "bad_config", "reason": ...}`)
until the env vars are fixed and the app restarts. `GET
/admin/emission/config` surfaces `disabled_reason` for exactly this case.

The split itself (`src/services/emission/scoring.py::split_emission`) is
integer wei math: `providers_wei`/`stakers_wei` are each
`floor(emission_wei * bps / 10000)`; `treasury_wei` is whatever's left
over (`emission_wei - providers_wei - stakers_wei`), so the three always
sum to exactly `emission_wei` regardless of floor-division dust. Treasury
also absorbs the provider-scoring pass's own floor-division dust (below)
-- WAYZ is never created or destroyed by rounding, it only ever rounds
*toward* the treasury.

## Provider scoring (trailing 7 days, ending the epoch date)

**Eligibility:** an approved provider with `>=1` verified `provider_work`
row OR `>=1` node currently `status='active'` in the window. A provider
with zero activity but that still qualifies (e.g. a brand-new active node
with no traffic yet) gets a `provider_scores` row with every metric and
`share=0` -- visible for transparency, paid nothing.

**Metrics**, each normalized to `[0, 1]`:

| Metric | Formula | Weight (`PROVIDER_SCORE_WEIGHT_*_BPS`) |
|---|---|---:|
| `compute` | `Σ(verified tokens × model-class weight)` ÷ max across this epoch's providers | 5500 (55%) |
| `speed` | `1 − clamp(provider_p50_latency_ms / network_median_p50_ms − 1, 0, 1)` | 2000 (20%) |
| `availability` | distinct hours with `>=1` completed work item ÷ 168 (7 days × 24h) | 2000 (20%) |
| `unique_models` | distinct models served ÷ max across this epoch's providers | 500 (5%) |

Model-class weight (`small`=1, `medium`=2, `large`=4) reuses the SAME
allow-list and testnet safety cap as per_unit payouts
(`src/services/gpu/earnings.py::effective_model_class`) -- an unknown/
not-on-the-allow-list model contributes 0 to `compute`, same "not
payable" treatment as today. `speed` compares each provider's own p50
latency against the **network's** median p50 (the median of every
scored provider's p50, not a raw median over all latency samples) --
at or below the network median scores 1.0, at 2x or worse scores 0.0,
linear in between. A provider with no latency samples this window scores
0 on `speed` (no data is the worst case, not a skip). `availability` is a
documented proxy: `gpu_nodes` only stores the single most recent
heartbeat, not history, so "active hours" is measured the same way
`src/db/gpu_rollups.py`'s public hourly rollup measures it -- hours with
at least one completed `provider_work` row -- rather than a literal
heartbeat count.

**Score:**

```
raw_score      = Σ(weight_i × metric_i)
adjusted_score = raw_score ** PROVIDER_SCORE_EXPONENT_ABOVE_MEDIAN   if raw_score >= median(raw_score across providers)
               = raw_score                                          otherwise
adjusted_score *= tier_multiplier_bps / 10000   # same log sliding-scale volume tiers as per_unit (provider_payout_tiers)
share_i        = adjusted_score_i / Σ(adjusted_score)                # 0 if the total is 0
allocation_wei = floor(providers_wei × share_i)
```

The median boundary is **inclusive** -- a provider exactly at the median
gets the exponent applied, same as everyone above it. The exponent
(default 1.3) rewards being above-median more than proportionally, same
as Chutes' formula. The tier multiplier is the SAME
`provider_payout_tiers` table and `tier_multiplier_bps()` function
per_unit payouts use, keyed off the same trailing-7d verified token
volume -- a big, sustained provider still gets a bigger multiplier than a
one-off node, exactly like today.

Every provider's `allocation_wei` floors, so `Σ(allocation_wei) <=
providers_wei`; the remainder ("dust") is folded into `treasury_wei` for
that epoch, never lost and never given to any one provider arbitrarily.

**Persistence:** one `provider_scores` row per (epoch_date, provider_id)
(all four raw metrics, `raw_score`, `adjusted_score`, `share`,
`tier_multiplier_bps`, `allocation_wei`), then one
`provider_earnings(source='emission', work_id=NULL, epoch_date=<date>,
status='accrued')` row per provider with `allocation_wei > 0`. Settlement
pays this identically to a per_unit earning (see
`docs/gpu/VERIFICATION_AND_PAYOUTS.md`'s Settlement section) -- no
settlement code needed to change for the nullable `work_id`.

## Staker payout (pro-rata to stake)

`stakers_wei` is split pro-rata to each linked-or-unlinked wallet's share
of total staked WAYZ (`wallet_stakes.staked_amount`) -- an unlinked
wallet accrues exactly like today's rate-table path (row written with
`user_id=NULL`, paid once linked or on a future retry sweep). Same
stale-chain guard as the per_unit job
(`src/services/staking_rewards.py::is_stake_sync_stale`) -- if
`wallet_stakes` hasn't synced recently enough to trust, the **entire**
`emission_epoch` run raises rather than pay off stale numbers, exactly
like `run_staking_rewards_once` does today.

`STAKER_REWARD_ASSET` (default `credits`) picks the payout asset:

- **`credits`** (default): `credits = wayz_amount × WAYZ_CREDIT_RATE`
  (default `0.001` credits per WAYZ), paid through the EXACT SAME
  `staking_reward_accruals` + `add_credits_to_user` insert-before-pay path
  the rate-table job uses (`source='emission'`, `wayz_amount_wei`
  recorded, `rate_id=NULL` since there's no rate-table row behind it).
  Same per-user daily cap (`STAKING_REWARDS_DAILY_CAP_CREDITS`) and
  minimum (`STAKING_REWARDS_MIN_CREDITS`, below which the day is
  `skipped` with `skip_reason='below_min'`) as today.
- **`wayz`**: **not implemented** -- there is no on-chain payout rail for
  raw WAYZ to a staker's wallet yet (unlike providers, who are paid via
  the existing `provider_settlements` WAYZ-transfer path). An accrual row
  is still written (`status='pending'`, `skip_reason=
  'wayz_payout_not_implemented'`, `wayz_amount_wei` recorded) so the
  amount owed is never lost -- it's just visibly unpaid until a WAYZ
  payout rail for stakers exists. The job's summary reports this
  explicitly (`staker_asset: "wayz"`, `stakers_paid: 0`) rather than
  silently dropping it.

## Worked example

`WAYZ_DAILY_EMISSION=100000`, default 41/41/18 split:

```
emission_wei   = 100000 WAYZ
providers_wei  =  41000 WAYZ  (41%)
stakers_wei    =  41000 WAYZ  (41%)
treasury_wei   =  18000 WAYZ  (18%, + any floor dust from below)
```

Three providers scored this epoch (weights 55/20/20/5, all at the 1.0x
payout tier for simplicity):

| Provider | compute | speed | availability | unique_models | raw_score |
|---|---:|---:|---:|---:|---:|
| A | 1.00 | 0.90 | 1.00 | 0.50 | 0.955 |
| B | 0.50 | 0.60 | 0.80 | 1.00 | 0.605 |
| C | 0.10 | 0.30 | 0.50 | 0.20 | 0.225 |

`raw_score = 0.55×compute + 0.20×speed + 0.20×availability + 0.05×unique_models`.
Median raw_score is B's `0.605` -- A and B (both `>= 0.605`) get the 1.3
exponent, C doesn't:

```
adjusted_A = 0.955 ** 1.3 ≈ 0.9419
adjusted_B = 0.605 ** 1.3 ≈ 0.5203
adjusted_C = 0.225             (unchanged, below median)
Σ adjusted ≈ 1.6872

share_A ≈ 0.9419 / 1.6872 ≈ 55.8%
share_B ≈ 0.5203 / 1.6872 ≈ 30.8%
share_C ≈ 0.2250 / 1.6872 ≈ 13.3%
```

Applied to `providers_wei = 41000`: A ≈ 22,888 WAYZ, B ≈ 12,644 WAYZ,
C ≈ 5,467 WAYZ (floor-to-wei; the small remainder from rounding these
three shares lands in `treasury_wei`, not any provider).

**Staker side:** network-wide 500,000 WAYZ staked; a staker holding 5,000
WAYZ (1% of the network) gets `1% × 41000 = 410` WAYZ/day worth of
credits: `410 × WAYZ_CREDIT_RATE (0.001) = 0.41` credits/day, capped at
`STAKING_REWARDS_DAILY_CAP_CREDITS` like any other accrual.

## Tuning

All of the above are env vars, no code changes needed to retune:

- `WAYZ_DAILY_EMISSION` -- the whole pool. Start conservative; this is a
  testnet placeholder (`100000`), not a production number the boss has
  confirmed.
- `EMISSION_SPLIT_{PROVIDERS,STAKERS,TREASURY}_BPS` -- must sum to 10000.
- `PROVIDER_SCORE_WEIGHT_{COMPUTE,SPEED,AVAILABILITY,UNIQUE_MODELS}_BPS`
  -- must also sum to 10000. Shifting weight toward `compute` rewards raw
  throughput more; toward `availability` rewards uptime/reliability more.
- `PROVIDER_SCORE_EXPONENT_ABOVE_MEDIAN` -- how much more the top half is
  rewarded relative to the bottom half. `1.0` disables the effect
  entirely (everyone's raw_score passes through unchanged).
- `STAKER_REWARD_ASSET` / `WAYZ_CREDIT_RATE` -- the staker payout asset
  and conversion rate.
- `EMISSION_EPOCH_CRON_HOUR_UTC` / `_MINUTE_UTC` (default `00:40` UTC) --
  when the daily job runs. Kept after `staking_rewards` (`00:20`) and
  `gpu_spot_check` so the trailing-7d verification data it scores against
  has had a chance to settle for the day.

## API

- `GET /gpu/providers/me/earnings` gains `emission: {last_epoch, score:
  {compute, speed, availability, unique_models, raw, adjusted, share},
  allocation_wayz, rank, providers_scored}` once a provider has been
  scored at least once (absent entirely before that, or while the
  feature is off).
- `GET /staking/rewards` gains `mode` (always) and `emission:
  {daily_emission_wayz, stakers_share_bps, your_share,
  estimated_credits_per_day}` (once emission mode has run at least one
  epoch), computed from the caller's own linked-wallet stake.
- `GET /gpu/public/summary` gains an aggregate-only `emission: {mode,
  daily_emission_wayz, providers_bps, stakers_bps, treasury_bps,
  last_epoch}` -- no per-provider or per-user data, keeping the endpoint's
  no-envelope, aggregate-only guarantee
  (`tests/security/test_gpu_public_aggregate_only.py`).
- Admin (`src/routes/admin_emission.py`, mirrors
  `src/routes/admin_staking.py`'s auth conventions):
  - `GET /admin/emission/epochs?limit` -- recent `emission_epochs` rows.
  - `GET /admin/emission/epochs/{date}` -- that day's `provider_scores`,
    highest share first.
  - `POST /admin/emission/run {epoch_date}` -- superadmin, audited
    (`emission.epoch.run`). Runs the job right now, same idempotency as
    the scheduled run; `409` with `error.context.parameter_value =
    "stake_sync_stale"` if the stake sync is too stale.
  - `GET /admin/emission/config` -- the effective env-derived config plus
    `disabled_reason` (non-null iff the startup bps-sum check failed).
  - `PUT /admin/emission/config` -- `501 Not Implemented`. Config is
    env-only today (unlike `staking_reward_rates`, there's no `kv` store
    backing a runtime override); change the env vars and restart, or use
    `POST /admin/emission/run` to test a one-off value.

## Idempotency

`emission_epochs.epoch_date` (PRIMARY KEY) is checked first
(`get_epoch()`) -- a re-run for an already-`'computed'`/`'allocated'`
date is a pure no-op. The per-row unique indexes are the DB-level
backstop for a crash mid-run: `provider_earnings`'
`idx_provider_earnings_emission` (`UNIQUE(provider_id, epoch_date) WHERE
source='emission'`) and `staking_reward_accruals`' existing
`UNIQUE(wallet_address, reward_date)` mean a retry after a partial
failure only creates what's missing -- whatever was already written comes
back as a 'duplicate'/'skipped' no-op, never a double-pay.

## Future work (explicitly out of scope this round)

- A `wayz` payout rail for stakers (see `STAKER_REWARD_ASSET=wayz` above).
- A `kv`-store-backed runtime override for `PUT /admin/emission/config`
  (env-only for now).
- Frontend: `/gpu` public "Emission" strip, `/gpu/provider` "Your score"
  card, `/staking` Earnings-card emission block (gatewayz-frontend).
