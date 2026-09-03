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
| `verified` | passes all applicable checks | `provider_earnings` row created (see below) |
| `failed` | empty reply, implausible token count, or low reference similarity | earning voided (if one existed), node `health_score -= 20`, node **disabled** after 3 failures in 24h |
| `skipped` | stash expired/missing, node/adapter unavailable this run | left unresolved; retried next run or resolved by the 24h aging path below |

A row that was `'sampled'` but never resolved (adapter unavailable, node
gone, etc.) is *not* stuck forever -- once it's 24h old it falls into the
aging path with `'pending'` rows.

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

`src/services/gpu/earnings.py`'s `model_class_for()` buckets a model id by
parsing its parameter count out of the id itself (e.g.
`community/llama-3.1-8b-instruct` → 8B → `small`). This is a **heuristic
against the model id string, not a real model-catalog lookup** -- a model
id with no parseable size (no `NNb` token) defaults to `medium` (logged),
a deliberate middle choice over silently over- or under-paying at an
extreme. **Known limitation:** revisit once a real catalog field for
parameter count exists.

Earnings math is **integer wei throughout**: `amount_wei = (prompt_tokens +
completion_tokens) * rate_wei_per_1k // 1000`, floored. Never floating
point at this magnitude.

## Settlement (daily, `COMMUNITY_SETTLEMENT_INTERVAL_HOURS`, default 24)

Per **approved** provider: sum `'accrued'` earnings; pay out iff the sum is
`>= COMMUNITY_MIN_PAYOUT_WAYZ` (default 10 WAYZ), the per-run cumulative
cap (`COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ`, default 100,000 WAYZ, shared
across all providers in one run) isn't exceeded, and the rewards pool's
live on-chain balance covers it.

- **Idempotent**: a provider with an already-`'pending'` settlement (a
  prior run that crashed mid-transfer) is skipped, not retried
  automatically -- that state needs manual investigation, since an
  automatic retry could double-pay if the original transfer actually
  landed on-chain.
- A transfer failure marks the settlement `'failed'` (with the error) and
  leaves the earnings `'accrued'` -- picked up again next run.
- An insufficient pool balance defers the provider without creating a
  settlement row at all (nothing to mark failed).
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
| `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` | unset | trusted provider slug for the similarity cross-check |
| `WAYZ_REWARDS_POOL_PRIVATE_KEY` | unset | `providerRewardsPool` EOA signing key; settlement no-ops until set |
| `COMMUNITY_MIN_PAYOUT_WAYZ` | `10` | minimum accrued balance to trigger a payout |
| `COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ` | `100000` | cumulative cap per settlement run |
| `COMMUNITY_SETTLEMENT_INTERVAL_HOURS` | `24` | settlement job interval |

## Future work (explicitly out of scope this round)

- **Signed-attestation-required verification**: today attestation only
  affects sampling *rate*; a stronger mode could require a valid
  `X-Gatewayz-Attestation` signature (spec §4, W-A2) before a request is
  even eligible for `'verified'`.
- **Merkle-claim settlement**: today the rewards pool sends a direct
  per-provider `transfer()` each run; a merkle-drop claim contract would
  reduce gas at scale but is unnecessary at testnet volume.
- **Real model-catalog parameter counts** for `model_class_for()`, instead
  of parsing the model id string.
