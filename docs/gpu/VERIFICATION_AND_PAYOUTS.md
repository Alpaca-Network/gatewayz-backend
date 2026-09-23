# Community GPU: verified work + ETH payouts

Covers gatewayz-backend#2265 (spot-check verification) and #2266
(earnings/settlement). See `m4/spec.md` §5 for the binding design; this
document is the operator/provider-facing reference plus the decisions made
where the spec was silent.

## Payout asset: USD-denominated, paid in native ETH on Base (2026-09-22)

WAYZ is not going public for now, so providers are **no longer paid in
WAYZ**. Decisions (migration `20260922120000_provider_payouts_eth_base.sql`):

- **Accrual is in USD**, as integer micro-dollars (1 USD = 1,000,000):
  `provider_payout_rates.usd_micros_per_1k_tokens` →
  `provider_earnings.amount_usd_micros`. Providers get a stable dollar
  rate; the treasury's cost is predictable.
- **Payout is native ETH on Base** (chain id 8453), a value transfer
  (EIP-1559 fees) from the payout pool EOA
  (`PROVIDER_PAYOUT_POOL_PRIVATE_KEY`) via
  `src/services/chain/eth_payout_client.py`. The gas limit is
  `eth_estimateGas` × 1.25 (min 21000) -- **never a hard-coded 21000**,
  because a payout wallet can be a smart-contract wallet (e.g. Coinbase
  Smart Wallet on Base) whose `receive()` costs more than a plain EOA
  transfer and would otherwise run out of gas and revert.
- **Paid means confirmed.** The tx is signed locally and its hash + nonce
  are written to the settlement row **before** broadcast. A settlement
  becomes `'sent'` (earnings `'settled'`) only on a receipt with
  `status == 1`. A receipt with `status == 0` (reverted: no value moved)
  fails the settlement and reverts the earnings to `'accrued'`. No
  receipt within `PROVIDER_PAYOUT_RECEIPT_TIMEOUT_SECONDS`, or a
  broadcast call that errors after the node may have accepted the tx,
  leaves the row `'pending'` (earnings `'settling'`) for the
  reconciliation sweep below -- **never reverted while its recorded hash
  could still land.**
- **Conversion happens at settlement time**, once per run, at the
  Chainlink ETH/USD aggregator on Base
  (`0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70`, verified on-chain:
  `description()=="ETH / USD"`, 8 decimals). `wei = usd_micros * 1e18 *
  10^decimals // (1e6 * answer)` -- floored, never overpays. If the answer
  is non-positive or older than `ETH_USD_PRICE_MAX_AGE_SECONDS` (default
  1800 ≈ 1.5× the feed's 1200s Base heartbeat, observed on-chain), or the
  **Base L2 sequencer-uptime feed**
  (`0xBCF85224fc0756B9Fa45aA7892530B47e10b6433`, verified on-chain) reports
  the sequencer down or back up for less than
  `BASE_SEQUENCER_GRACE_PERIOD_SECONDS` (default 3600), or anything can't
  be read, **the whole run aborts and nobody is paid** -- earnings stay `'accrued'` and the
  `gpu_settlement` job run is recorded as failed. The price and its
  `updatedAt` are stored on every `provider_settlements` row
  (`eth_usd_price`, `price_updated_at`, `asset='ETH'`, `chain='base'`).
- **Legacy WAYZ rows are never paid in ETH.** Rows accrued before the
  switch have `amount_usd_micros IS NULL` (only `amount_wei`); the
  settlement queries only claim rows with a USD amount. The WAYZ/wei
  columns are kept (nullable) for history and surfaced as
  `legacy_wayz_*_wei` on the earnings endpoints.
- `PROVIDER_PAYOUT_ASSET` (default `ETH`) is the switch; `ETH` is the only
  supported value -- anything else keeps the settlement scheduler from
  starting (logged at ERROR).

## Two payout modes: `per_unit` (this doc) vs `emission`

Everything below describes `Config.REWARDS_MODE`'s default, `per_unit`:
a provider is accrued a `provider_earnings` row **per verified work item**
(this doc's payout-rate table below). The alternative,
**`emission`** (Chutes-style emission rewards,
`docs/tokenomics/EMISSION.md`), pays providers a **daily share of a fixed
USD pool** (`PROVIDER_EMISSION_USD_PER_DAY`, paid in ETH) by 7-day
rolling score instead -- verification
itself (sampling, replay, the aging path, `provider_work.verification`)
is completely unchanged either way; only what happens to a `'verified'`
row differs:

- `per_unit`: `record_earning_for_verified_work` accrues a
  `provider_earnings(source='per_unit', work_id=<row>)` earning, per the
  rate table below.
- `emission`: `record_earning_for_verified_work` returns immediately
  (`outcome='skipped_emission_mode'`) without creating a per-unit earning
  -- the row still gets marked `'verified'` (verification bookkeeping is
  unaffected), it just isn't paid per-item. Instead, the daily
  `emission_epoch` job aggregates the trailing 7 days of `'verified'` work
  per provider into a score and creates ONE
  `provider_earnings(source='emission', work_id=NULL, epoch_date=<date>)`
  allocation per provider per day. Settlement (below) pays either kind of
  earning identically -- its queries never select or filter on `work_id`.

The two modes are mutually exclusive by construction: the emission job
only runs once `REWARDS_MODE=='emission'`, and `record_earning_for_verified_work`
checks that same flag before ever touching the per-unit rate table.

## Why replay instead of trusting the report

A community GPU node is an untrusted party by design (M4's trust-boundary
decision, spec §1) -- it reports its own `prompt_tokens`/`completion_tokens`
and could lie about which model it ran. We never store prompt/response
content (threat model G3), so verification means replaying a small sample
of requests against the node itself.

## Sampling (pre-sampling)

Because prompts aren't stored, verification must be decided **before** a
community request is forwarded, not after -- otherwise there's nothing to
replay. `src/services/gpu/spot_check.py`'s `maybe_stash(billing_ref,
messages, model, node)` is the integration point the community routing
path (W-A2) calls right before forwarding to a node:

- Sampling probability is `COMMUNITY_SPOTCHECK_RATE` (default 5%), **doubled**
  (capped at 100%) when the node has no attestation history
  (`node.attested_heartbeat` falsy) -- unattested nodes get more scrutiny.
- A sampled request's prompt is stashed in Redis
  (`gpu_spotcheck:{billing_ref}`, 20 minute TTL) -- long enough for the
  10-minute verifier job to pick it up, short enough to bound the exposure
  window of prompt content sitting in Redis.
- The caller (W-A2's `record_work()`) must set `provider_work.verification
  = 'sampled'` iff `maybe_stash` returned `True`, else leave the column at
  its `'pending'` default. This module never writes that column itself on
  the request path.

## Verification job (every `COMMUNITY_SPOTCHECK_INTERVAL_MINUTES`, default 10)

For each `'sampled'` row from the last hour with a live stash:

1. Replay the stashed prompt on the **same node**, `temperature=0`,
   `max_tokens = min(64, claimed completion_tokens)`.
2. **Decision (spec §5 names one check, "same-node determinism", without
   fully specifying its comparison partner):** if
   `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` is configured, also replay on
   that trusted provider and require `difflib.SequenceMatcher` prefix
   similarity `>= 0.8` over the first 64 tokens of both replies. Without a
   reference configured (the default -- none is deployed today), there is
   nothing truthful to diff the node's reply against, so the similarity
   check is skipped and verification falls back to the checks below alone.
   This is intentionally a weaker guarantee, which is part of why
   unattested nodes get double the sampling rate.
3. Non-empty reply required.
4. Replayed `completion_tokens` must be within ±25% of the tokens the node
   originally claimed for that request.

**Outcomes:**

| Outcome | Trigger | Effect |
|---|---|---|
| `verified` | passes all applicable checks AND the model is payable (see below) | `provider_earnings` row created |
| `failed` | empty reply, implausible token count, or low reference similarity | earning voided (if one existed), node `health_score -= 20`, node **disabled** after 3 failures in 24h |
| `skipped` | stash expired/missing, node/adapter unavailable this run, per-run/per-node replay cap reached, or model not payable | left unresolved (first three) or terminal-but-unpaid (model not payable) |

A row that was `'sampled'` but never resolved (adapter unavailable, node
gone, replay cap reached, etc.) is *not* stuck forever -- once it's 24h
old it falls into the aging path below (which now also picks up
`'sampled'` rows, not just `'pending'` ones).

**Replay budget (PR #2288 review I2):** the job is sequential with a
small delay between replays (`COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS`,
default 0.5s), bounded by `COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN`
(default 50, global) and `COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN`
(default 5, per node) -- so one busy or misbehaving node's queue of
sampled rows can't monopolize a run, and the job itself can't run
unbounded or hammer a node with a burst of replay traffic. Rows past
either cap are simply deferred (left `'sampled'`) to a later run or the
24h aging path.

## Unsampled / unresolved rows (24h aging)

A row that was never sampled (the common case, ~95% of traffic by
default) or was sampled but never resolved is picked up once it's more
than 24h old:

- Compute the node's failure rate over the last 24h (`failed / (verified +
  failed)` among that node's *resolved* rows).
- Failure rate `< 5%` → `verified` **and paid**.
- Failure rate `>= 5%` (or no history) → `skipped`, unpaid.

This is deliberately never `'failed'` -- there's no evidence this specific
unreplayed row was bad, only that the node's recent track record is poor.

## Payout rates (`provider_payout_rates`, seeded by W-A1's migration)

| Class | Parameter count | Rate |
|---|---|---|
| `small` | ≤ 13B | `usd_micros_per_1k_tokens` = 20000 ($0.02) -- PLACEHOLDER |
| `medium` | ≤ 34B | `usd_micros_per_1k_tokens` = 50000 ($0.05) -- PLACEHOLDER |
| `large` | > 34B | `usd_micros_per_1k_tokens` = 100000 ($0.10) -- PLACEHOLDER |

(The legacy `wayz_per_1k_tokens` column is kept but no longer read.)

### Model class is an exact allow-list, not a parse of the reported id (C1)

`gpu_nodes.models` and `provider_work.model` are **provider-declared,
free-text strings**. The original design (before PR #2288's review)
regexed a parameter count straight out of that string -- which meant a
dishonest node could self-report a model id like
`community/definitely-a-70b-model`, get bucketed into `large` (5x the
`small` rate) purely from the string, and actually run whatever cheap
model it wanted underneath, returning plausible-length filler that
passes the non-empty/token-count checks. This was a real, exploitable
payout-inflation vector, not just an accuracy nit.

**Fix**: `src/services/gpu/model_classes.py` is an exact-match allow-list
of real open-weight model ids, seeded with ~10 well-known instruct models
across the three size classes. `earnings.py`'s `model_class_for()` only
ever returns a class for an id ON that list -- an id that isn't listed is
simply **not payable** (verification is written as `'skipped'`, not
`'verified'`, and it's logged). Extend the list by editing
`_BUILTIN_MODEL_CLASSES`, or without a deploy via
`COMMUNITY_MODEL_CLASS_OVERRIDES` (a JSON object string, e.g.
`{"some-new-model-id": "medium"}`).

**W-A1 follow-up (not implemented here -- `src/routes/gpu.py` doesn't
exist in this worktree yet):** node registration (`POST /gpu/nodes`)
should reject or warn on a declared model id that isn't on this allow-list
too, using `model_classes.is_known_model_id()`, so an operator finds out
at registration time rather than discovering their traffic is unpaid.

### Testnet safety cap: `medium`/`large` rates require attestation + a reference provider

Even for a KNOWN model, this PR's fix round 1 caps every request down to
the `small` rate unless **both**:

1. the work item carries a valid attestation (`provider_work.attested`), **and**
2. `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` is configured (i.e. the
   strongest verification path -- the reference-provider similarity
   check -- is actually active for spot-checks on this deployment).

Without both, the allow-list still prevents an unknown model from being
paid at all, but a node could still misreport OUTPUT QUALITY within the
`small` rate (the token-count/non-empty checks alone are weak). Capping
to `small` bounds that residual risk to a 1x multiplier instead of up to
5x, until `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` is provisioned and
attestation is common. **Recommended before enabling
`COMMUNITY_ROUTING_ENABLED` for real traffic: provision a reference
provider.**

`src/services/gpu/earnings.py`'s `effective_model_class()` implements
this; `model_class_for()` alone (no cap) is only used to decide payability.

Earnings math is **integer USD micros throughout**: `amount_usd_micros =
(prompt_tokens + completion_tokens) * rate_usd_micros_per_1k // 1000`,
floored. Never floating point for money.

### Log sliding-scale volume tiers (`provider_payout_tiers`)

On top of the model-class rate above, every payout is scaled by a
**basis-points multiplier** keyed off the provider's own trailing-7-day
**verified** token volume:

```
amount_usd_micros = ((prompt_tokens + completion_tokens) * rate_usd_micros_per_1k // 1000)
                    * multiplier_bps // 10000
```

Both divisions floor, same integer-only rule as the base rate. The tier
table (`provider_payout_tiers`: `min_tokens_7d`, `multiplier_bps`, `label`)
is seeded with these **testnet placeholders**:

| Trailing-7d verified tokens | Multiplier | Label |
|---|---|---|
| 0 | 0.05x (500 bps) | `bot` |
| 100,000 | 0.25x (2500 bps) | `small` |
| 1,000,000 | 0.60x (6000 bps) | `medium` |
| 10,000,000 | 1.00x (10000 bps) | `large` |
| 100,000,000 | 1.50x (15000 bps) | `whale` |

**Product rationale**: a one-off/"bot" node that shows up, does a
handful of requests, and disappears should earn almost nothing, while a
large, sustained community provider should be rewarded well above the
flat per-token rate. A basis-points multiplier keeps every step of the
math integer (never floats for money).

**Tunable without a deploy**: edit `provider_payout_tiers` directly
(insert/update/delete rows) -- `src/db/gpu_payouts.py`'s
`get_payout_tiers()` caches for only ~60s, so a change is live almost
immediately. `src/services/gpu/earnings.py`'s `tier_multiplier_bps()`
picks the largest tier whose `min_tokens_7d <= volume`; an empty table
(mid-migration, or wiped by mistake) pays the full 1.0x rate rather than
zeroing out every payout.

**Sybil note -- volume is per PROVIDER, not per node.**
`get_provider_verified_volume_7d(provider_id)` sums `provider_work` rows
by `provider_id` (the payout wallet), never by `node_id`. Registering
many small nodes under the same provider account does not reset or split
anyone's tier -- all of that provider's verified traffic, across every
node they run, counts toward the same trailing-7d volume. Splitting
traffic across separate `gpu_providers` accounts (separate payout
wallets, separate KYC/approval) is a different, much higher-friction
attack that this feature does not attempt to solve.

**Volume includes the work item currently being paid.** Because
`record_earning_for_verified_work` runs BEFORE `provider_work.verification`
is flipped to `'verified'` for that row (see `_apply_sampled_outcome`
above), a naive query would miss a provider's very first verified item.
`get_provider_verified_volume_7d` takes an `exclude_work_id` so the
current row's own tokens can be added back in exactly once, correctly on
both the common path (row not yet verified in the DB) and the
reconciliation-retry path (row already verified in the DB).

Both `multiplier_bps` and `volume_7d_at_accrual` are stored on the
`provider_earnings` row at accrual time (nullable columns, added by
`20260909000000_provider_payout_tiers.sql`) for auditability -- so a
past payout's tier can always be explained without recomputing a 7-day
window retroactively. Pre-existing rows (accrued before this migration)
are left `NULL`, not backfilled.

### Reconciling a lost payout (I1)

`create_earning`'s insert can fail for a reason that ISN'T a duplicate
work_id (network blip, RLS misconfig, malformed payload). That case is
logged at WARNING (not INFO, which is reserved for genuine duplicates)
and the work item is left `'verified'` -- unpaid, but not permanently:
every verifier job run also calls `_reconcile_missing_earnings()`, which
re-attempts `record_earning_for_verified_work` for every row verified in
the last `COMMUNITY_EARNINGS_RECONCILE_LOOKBACK_HOURS` (default 48h).
Since that function is idempotent (the UNIQUE(work_id) constraint turns
an already-paid row's re-attempt into a cheap no-op), this is safe to run
every single job cycle rather than needing to track which specific rows
failed.

## Settlement (daily, `COMMUNITY_SETTLEMENT_INTERVAL_HOURS`, default 24)

Pays `'accrued'` earnings regardless of `source` -- an `emission`-mode
allocation (`work_id IS NULL`) is summed, claimed, and transferred exactly
like a `per_unit` one, because every query in `src/services/gpu/settlement.py`
(`list_accrued_earnings`, `mark_earnings_settling`/`settled`/`accrued`)
filters on `provider_id`/`status`/`settlement_id` only -- none of them
ever select or filter on `work_id`. No settlement code changed to support
emission mode; see `tests/services/gpu/test_settlement.py`'s null-`work_id`
coverage.

Per **approved** provider: preview `'accrued'` USD earnings; pay out iff
the preview sum is `>= COMMUNITY_MIN_PAYOUT_USD` (default $5), the per-run
cumulative cap (`COMMUNITY_MAX_PAYOUT_PER_RUN_USD`, default $5,000, shared
across all providers in one run) isn't exceeded, and the payout pool's
live ETH balance minus `PROVIDER_PAYOUT_GAS_RESERVE_WEI` (default 0.001
ETH, left behind for gas) covers the converted wei amount.

- **Idempotent**: a provider with an already-`'pending'` settlement (a
  prior run that crashed mid-flight) is skipped by `run_settlement_once`
  itself -- it's picked up instead by the reconciliation sweep below,
  automatically, once it's old enough.
- A transfer failure marks the settlement `'failed'` (with the error) and
  reverts its claimed earnings back to `'accrued'` -- picked up again
  next run.
- An insufficient pool balance (checked against the cheap preview) defers
  the provider without creating a settlement row at all.

### Race-safety: the atomic "settling" flip (I4)

The preview read (`list_accrued_earnings`) is a cheap filter, **not** the
authoritative amount. Before any transfer, the job calls
`mark_earnings_settling(provider_id, settlement_id)` -- a single `UPDATE
provider_earnings SET status='settling', settlement_id=... WHERE
provider_id=... AND status='accrued'`, and sums exactly the rows THAT
returned. Because this is one atomic SQL statement, a concurrent
spot-check failure's `void_earning_for_work` (which only ever matches
`status='accrued'`) can never touch a row after this has claimed it into
`'settling'`, and this can never claim a row a concurrent void got to
first. If the authoritative post-flip total falls outside a threshold
(a race did shrink it, say), the claimed earnings are reverted back to
`'accrued'` and the settlement is marked failed -- no wei is sent below
the configured minimum or above a cap because of a race. `provider_earnings.status`
now has a 4th value, `'settling'` (migration
`20260903200001_provider_earnings_settling.sql`, additive to W-A1's
migration since it hadn't merged when this was written).

### Stuck-pending reconciliation runbook (I3)

Every settlement run first sweeps `'pending'` `provider_settlements`
rows (`reconcile_stuck_settlements`). Because the tx hash and nonce are
recorded **before** broadcast, the rules are:

1. **No `tx_hash`**, older than `COMMUNITY_SETTLEMENT_STUCK_HOURS`
   (default 2h): the run died before signing/recording, so nothing was
   ever broadcast → mark `'failed'`, revert earnings to `'accrued'`.
   Younger no-hash rows are left alone (a run may be in flight).
2. **Receipt `status == 1`** (any age) → mark `'sent'`, earnings
   `'settled'`.
3. **Receipt `status == 0`** (any age) → mined but reverted, no value
   moved → mark `'failed'`, revert earnings.
4. **No receipt, and the pool's mined nonce is past `tx_nonce`** →
   another tx consumed that nonce, so this hash can never land. The
   receipt is re-checked once (guards a flaky RPC), then `'failed'` +
   revert.
5. **No receipt, nonce not yet consumed** (or the receipt lookup errored)
   → **left `'pending'`**, logged at WARNING with its hash and nonce. The
   tx could still land, so reverting would risk a double pay. It resolves
   on its own once the pool mines any later tx (a dropped tx's nonce gets
   reused). The provider is skipped by settlement while it's pending.

**Operator check** for a long-pending row: look up its `tx_hash` and the
pool EOA on [Basescan](https://basescan.org). If the tx is truly gone
from the mempool and you need the nonce freed, send any 0-value tx from
the pool at that nonce; the next sweep then resolves it via rule 4.

- **No-op until funded**: while `PROVIDER_PAYOUT_POOL_PRIVATE_KEY` is
  unset the scheduler doesn't even start, matching the WAYZ staking
  sync's and faucet's established "unset config → clean no-op" pattern.
  Earnings still accrue (in USD) normally in the meantime; nothing is
  lost, payout is just deferred until the key is provisioned and the EOA
  holds ETH on Base.

## Provider-facing endpoint

`GET /gpu/providers/me/earnings` (auth) returns totals as
`{accrued,settled,void}_usd` (decimal string) and `_usd_micros` (int) --
the source of truth -- plus ETH amounts that existing clients already
read, kept meaningful (never zeroed):

- `settled_wei` / `settled_eth`: ETH **actually paid** on Base (sum of
  confirmed `'sent'` ETH settlements).
- `accrued_wei` / `void_wei` (and `settling_wei` on the admin endpoint):
  the USD balance converted at the current trusted ETH/USD price (the
  same sequencer + staleness gate settlement uses, cached 5 min).
  `null` only while no trusted price is available.
- `eth_usd_price` (the display price used), `payout_asset: "ETH"`,
  `payout_chain: "base"`, and `legacy_wayz_{status}_wei` for pre-switch
  WAYZ history.

It also returns the last 50 `provider_work` rows (billing_ref, model,
token counts, verification -- no hashes), and settlements with `asset`,
`chain`, `amount_usd`, `amount_wei`/`amount_eth` (in the settlement's
asset), `eth_usd_price`, `confirmed` (true only after a status-1
receipt), and a `https://basescan.org/tx/{tx_hash}` link (Snowtrace for
legacy WAYZ rows). The emission block keeps `allocation_wayz` as a
deprecated alias for `allocation_eth` (the USD allocation's ETH
equivalent). Finally there's a `tier`
object so an operator can see where they stand on the sliding scale:
`{current_volume_7d, multiplier_bps, next_tier_min_tokens_7d}` --
`next_tier_min_tokens_7d` is `null` once the provider is at (or above) the
top tier. Always scoped to the caller's own provider row -- never a
client-supplied `provider_id`.

## Config reference

| Var | Default | Meaning |
|---|---|---|
| `COMMUNITY_SPOTCHECK_RATE` | `0.05` | base sampling probability |
| `COMMUNITY_SPOTCHECK_INTERVAL_MINUTES` | `10` | verifier job interval |
| `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` | unset | trusted provider slug for the similarity cross-check; also gates the `medium`/`large` payout-rate safety cap |
| `COMMUNITY_MODEL_CLASS_OVERRIDES` | unset | JSON object string adding/overriding `model_classes.py`'s allow-list, e.g. `{"some-id": "medium"}` |
| `COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN` | `50` | global cap on live replay calls per verifier job run |
| `COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN` | `5` | per-node cap on live replay calls per run |
| `COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS` | `0.5` | delay between sequential replay attempts |
| `COMMUNITY_EARNINGS_RECONCILE_LOOKBACK_HOURS` | `48` | how far back the verifier job retries missing-earnings recovery |
| `PROVIDER_PAYOUT_ASSET` | `ETH` | payout asset; only `ETH` is supported |
| `PROVIDER_PAYOUT_POOL_PRIVATE_KEY` | unset | payout pool EOA signing key (holds ETH on Base); settlement no-ops until set |
| `BASE_RPC_URL` | `https://mainnet.base.org` | Base JSON-RPC (use a paid endpoint in prod) |
| `BASE_CHAIN_ID` | `8453` | Base mainnet |
| `BASE_ETH_USD_FEED_ADDRESS` | `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70` | Chainlink ETH/USD aggregator on Base |
| `ETH_USD_PRICE_MAX_AGE_SECONDS` | `1800` | older feed answer → run aborts, nobody paid (~1.5× the 1200s heartbeat) |
| `BASE_SEQUENCER_UPTIME_FEED_ADDRESS` | `0xBCF85224fc0756B9Fa45aA7892530B47e10b6433` | Chainlink L2 sequencer-uptime feed; empty disables the check |
| `BASE_SEQUENCER_GRACE_PERIOD_SECONDS` | `3600` | no payouts until the sequencer has been up this long |
| `PROVIDER_PAYOUT_RECEIPT_TIMEOUT_SECONDS` | `120` | per-payout receipt wait before leaving it pending for reconcile |
| `PROVIDER_PAYOUT_GAS_RESERVE_WEI` | `1000000000000000` | ETH always left in the pool for gas |
| `COMMUNITY_MIN_PAYOUT_USD` | `5` | minimum accrued USD to trigger a payout |
| `COMMUNITY_MAX_PAYOUT_PER_RUN_USD` | `5000` | cumulative USD cap per settlement run |
| `PROVIDER_EMISSION_USD_PER_DAY` | `100` | emission mode only: USD/day split across providers by score |
| `COMMUNITY_SETTLEMENT_INTERVAL_HOURS` | `24` | settlement job interval |
| `COMMUNITY_SETTLEMENT_STUCK_HOURS` | `2` | how long a settlement can sit `'pending'` before the automatic reconciliation sweep resolves it |

## Future work (explicitly out of scope this round)

- **Signed-attestation-required verification for `'verified'` itself**:
  today attestation affects sampling *rate* and (as of this fix round)
  the payout-rate cap, but doesn't gate verification eligibility itself;
  a stronger mode could require a valid `X-Gatewayz-Attestation`
  signature (spec §4, W-A2) before a request is even eligible for
  `'verified'`.
- **Merkle-claim settlement**: today the rewards pool sends a direct
  per-provider `transfer()` each run; a merkle-drop claim contract would
  reduce gas at scale but is unnecessary at testnet volume.
- **Real model-catalog membership/parameter counts** to drive
  `model_classes.py`'s allow-list, instead of a hand-curated dict +
  env-var overrides.
- **Node-registration-time allow-list enforcement** (`POST /gpu/nodes`,
  W-A1) using `model_classes.is_known_model_id()` -- not implemented here
  since that route doesn't exist in this worktree yet.
- **A real reconciliation dashboard/alert** for settlements the automatic
  sweep marks failed, rather than relying on an operator reading logs.
