# Community GPU: verified work + WAYZ payouts

Covers gatewayz-backend#2265 (spot-check verification) and #2266 (WAYZ
earnings/settlement). See `m4/spec.md` §5 for the binding design; this
document is the operator/provider-facing reference plus the decisions made
where the spec was silent.

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
| `small` | ≤ 13B | seeded `wayz_per_1k_tokens` (wei) |
| `medium` | ≤ 34B | seeded `wayz_per_1k_tokens` (wei) |
| `large` | > 34B | seeded `wayz_per_1k_tokens` (wei) |

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

Earnings math is **integer wei throughout**: `amount_wei = (prompt_tokens +
completion_tokens) * rate_wei_per_1k // 1000`, floored. Never floating
point at this magnitude.

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

Per **approved** provider: preview `'accrued'` earnings; pay out iff the
preview sum is `>= COMMUNITY_MIN_PAYOUT_WAYZ` (default 10 WAYZ), the
per-run cumulative cap (`COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ`, default
100,000 WAYZ, shared across all providers in one run) isn't exceeded, and
the rewards pool's live on-chain balance covers it.

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

Every settlement run first sweeps `provider_settlements` rows stuck
`'pending'` for longer than `COMMUNITY_SETTLEMENT_STUCK_HOURS` (default
2h) -- the crash window between `create_settlement`/
`mark_earnings_settling` and `mark_settlement_sent`/
`mark_earnings_settled`. For each stuck row:

1. **`tx_hash` present AND its on-chain receipt shows success**
   (`status == 1`) → confirm it: mark `'sent'`, flip its `'settling'`
   earnings to `'settled'`.
2. **Everything else** -- no `tx_hash` at all (crashed before `transfer()`
   even returned), a receipt showing an on-chain revert (`status == 0`),
   or no receipt found after being stuck this long (very likely
   dropped/never broadcast on a ~2s-block chain) -- mark `'failed'` and
   revert its earnings to `'accrued'` so a future run retries them.

**The one real risk this automatic sweep creates**: if the original
transaction is somehow still in flight (e.g. a slow/congested RPC) and
lands on-chain LATER than the 2h threshold, a provider whose earnings
were reverted-and-retried by the sweep would end up paid twice once both
transfers land. **Manual check before trusting an automatic-sweep
outcome you're unsure about**: look up the pool EOA's address on
[Snowtrace testnet](https://testnet.snowtrace.io) and search its recent
transaction history for the swept settlement's `error` field's mention of
a `tx_hash` (if any) or the timeframe around its `created_at` -- confirm
nothing landed before manually re-approving further settlement for that
provider.

- **No-op today**: `WAYZ_REWARDS_POOL_PRIVATE_KEY` is unset (nothing
  deployed to the `providerRewardsPool` EOA on Fuji yet) -- the scheduler
  doesn't even start until it's configured, matching the WAYZ staking
  sync's and faucet's established "unset config → clean no-op" pattern.
  Earnings still accrue normally in the meantime; nothing is lost, payout
  is just deferred until the key is provisioned.

## Provider-facing endpoint

`GET /gpu/providers/me/earnings` (auth) returns wei-string totals
(`accrued_wei`/`settled_wei`/`void_wei`), the last 50 `provider_work` rows
(billing_ref, model, token counts, verification -- no hashes), and
settlements with a `https://testnet.snowtrace.io/tx/{tx_hash}` link.
Always scoped to the caller's own provider row -- never a client-supplied
`provider_id`.

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
| `WAYZ_REWARDS_POOL_PRIVATE_KEY` | unset | `providerRewardsPool` EOA signing key; settlement no-ops until set |
| `COMMUNITY_MIN_PAYOUT_WAYZ` | `10` | minimum accrued balance to trigger a payout |
| `COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ` | `100000` | cumulative cap per settlement run |
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
