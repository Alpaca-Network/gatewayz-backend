# Usage anchoring on Avalanche C-Chain

Once a day, Gatewayz writes the Merkle root of the previous UTC day's
inference usage records to the `UsageAnchor` contract on Avalanche C-Chain.
Anyone can see the daily roots on-chain; each user can fetch an inclusion
proof for their own records and check it against the chain without trusting
Gatewayz.

- Contract: `UsageAnchor.sol` in `Alpaca-Network/gatewayz-protocol`
  (append-only; one root per day; only the immutable `anchorer` can write).
- Job: `src/services/chain/usage_anchor.py`, scheduled by
  `src/services/scheduled_sync.py::start_usage_anchor_scheduler`.
- Chain client: `src/services/chain/usage_anchor_client.py`.
- Merkle tree: `src/services/chain/merkle.py` (RFC 6962).
- Table: `usage_anchors` (`supabase/migrations/20261020000000_usage_anchors.sql`).
- API: `src/routes/usage_anchors.py`.
- Verifier: `scripts/verify_usage_anchor.py` (standard library only).

## Why

- **Billing you can check.** A user's charges are committed to a public root
  the next day; we can't quietly change a past day's records afterwards
  without the root no longer matching.
- **Groundwork for on-chain settlement.** Daily usage roots are the simplest
  form of compute settlement data, ahead of any dedicated chain.
- It moves no money and involves no token. One small transaction per day.

## What is anchored (leaf format v1)

Source: `credit_transactions` rows with `transaction_type = 'api_usage'`.
This is the authoritative per-request debit, written in the same database
transaction as the balance change (`atomic_deduct_credits`). Rejected
alternatives: `chat_completion_requests` is an analytics table (it skips
rows whose model isn't in the catalog, and stores no cost on the legacy
path); `credit_ledger` is the Phase 3 shadow ledger, not yet authoritative.
Refunds (`transaction_type = 'refund'`) are separate rows and are **not**
leaves in v1.

Each row becomes the UTF-8 bytes of canonical JSON (sorted keys, separators
`,` and `:`, no whitespace):

```json
{"cost":"-0.00120000","id":"123456","model":"anthropic/claude-sonnet-4-6","salt":"<64 hex>","tokens_in":100,"tokens_out":20,"ts":"2026-10-18T12:00:00.000000Z","v":1}
```

| Field | Source |
|---|---|
| `v` | always `1` |
| `id` | `credit_transactions.id`, as a string |
| `ts` | `created_at` in UTC, microseconds, `Z` suffix |
| `model` | `metadata.model` (or `null`) |
| `tokens_in` / `tokens_out` | `metadata.prompt_tokens` / `metadata.completion_tokens` (0 if absent) |
| `cost` | `amount` as stored, 8 decimal places (negative = debit) |
| `salt` | `HMAC-SHA256(USAGE_ANCHOR_SALT_KEY, id)` as hex |

Leaves are ordered by `(created_at, id)`. One tree per UTC day.

## Merkle spec

RFC 6962 over SHA-256: `leaf = SHA-256(0x00 || leaf_bytes)`,
`node = SHA-256(0x01 || left || right)`, split at the largest power of two
below `n`. Proofs are RFC 6962 audit paths, verified per RFC 9162 §2.1.3.2.
The root goes on-chain as `UsageAnchor.anchor(day, root, leafCount)` with
`day` = UTC days since 1970-01-01. A day with no usage is not anchored.

## Privacy rule

- **Only roots go on-chain.** Leaves never leave the database, except that a
  user can fetch the leaf (with its salt) for their **own** record through
  the proof endpoint.
- Leaves contain no user id, email, API key, request id, prompt or response.
- The per-leaf salt stops anyone guessing a leaf from public information
  (e.g. a known model and token count). Keep `USAGE_ANCHOR_SALT_KEY` secret
  and **never rotate it**: changing it changes every past leaf, and
  rebuilt roots would stop matching the chain.

## Job behaviour

- Daily at `USAGE_ANCHOR_HOUR_UTC:USAGE_ANCHOR_MINUTE_UTC` (default 00:15 UTC),
  for the previous day. A sweep every `USAGE_ANCHOR_SWEEP_INTERVAL_MINUTES`
  (default 10) confirms pending transactions.
- Idempotent: a `confirmed` day is never touched again.
- **Never re-anchors a day with a different root.** If the rebuilt root
  differs from the stored or on-chain root, the row is marked `failed` and an
  alert goes to Sentry and the ERROR log. A human investigates; a `failed`
  row is left alone by the job and the admin trigger until someone deletes
  or corrects it in `usage_anchors`.
- The tx hash is stored before broadcast. A receipt that vanishes (reorg) or
  a nonce that was replaced marks the row `reorged`; it is resubmitted
  **once**, then marked `failed`.
- A send that never reached the network (RPC down, unfunded signer) leaves
  the day `unanchored`; the sweep retries days up to 7 days old.
- Before sending, the client checks the RPC's chain id and that the signer
  is the contract's `anchorer`.
- All DB and RPC work runs in a worker thread, never on the event loop.
  Runs are serialized in-process; the contract's one-root-per-day rule is the
  backstop if more than one worker ever runs the job.

## API

| Route | Auth | What |
|---|---|---|
| `GET /.well-known/anchors.json` | none | chain, contract, anchorer, spec, latest 30 anchors with Snowtrace links |
| `GET /v1/anchors?limit=` | none | anchors (max 366) |
| `GET /v1/anchors/{YYYY-MM-DD}` | none | one day |
| `GET /v1/anchors/proof/{ledger_id}` | API key | inclusion proof for the caller's own record (404 for anyone else's) |
| `POST /admin/usage-anchor/run` `{"day": "YYYY-MM-DD"}` | superadmin, audited | anchor a finished day now (backfill / demo) |

Verify a proof:

```bash
curl -s -H "Authorization: Bearer $KEY" https://api.gatewayz.ai/v1/anchors/proof/123456 > proof.json
python scripts/verify_usage_anchor.py proof.json          # uses the public RPC for the proof's chain
python scripts/verify_usage_anchor.py proof.json --offline  # Merkle check only
```

## Ops

### Environment variables (Railway `api` service)

| Var | Default | Notes |
|---|---|---|
| `USAGE_ANCHOR_ENABLED` | `false` | scheduler only starts when true |
| `USAGE_ANCHOR_CHAIN_ID` | `43113` (Fuji) | `43114` for mainnet |
| `USAGE_ANCHOR_RPC_URL` | Fuji public RPC | mainnet: `https://api.avax.network/ext/bc/C/rpc` or a paid RPC |
| `USAGE_ANCHOR_CONTRACT_ADDRESS` | — | from the `gatewayz-protocol` deployment artifact |
| `USAGE_ANCHOR_PRIVATE_KEY` | — | **dedicated** key; refused if equal to the payout-pool key; never logged |
| `USAGE_ANCHOR_SALT_KEY` | — | 32+ random bytes, hex; never rotate |
| `USAGE_ANCHOR_HOUR_UTC` / `_MINUTE_UTC` | `0` / `15` | daily run time |
| `USAGE_ANCHOR_MIN_CONFIRMATIONS` | `3` | |
| `USAGE_ANCHOR_SWEEP_INTERVAL_MINUTES` | `10` | |

### Keys

```bash
cast wallet new                                # anchor signer: address + private key
python -c "import secrets; print(secrets.token_hex(32))"   # salt key
```

The signer's address is the `anchorer` passed to the contract constructor, so
generate it before deploying. Store both secrets only in Railway; never in
git, never in chat.

### Funding

One `anchor()` call per day, roughly 50–60k gas. Fund the signer with a
small amount of AVAX (Fuji: faucet; mainnet: a small top-up) and watch its
balance. An unfunded signer leaves days `unanchored` and the sweep retries.

### Rollout

1. Apply the migration (`supabase/migrations/20261020000000_usage_anchors.sql`).
2. **Fuji:** deploy `UsageAnchor` with the signer as `anchorer`; set the env
   vars with `USAGE_ANCHOR_CHAIN_ID=43113`; enable; anchor a past day with the
   admin trigger; verify a proof with the script; let it run daily for a week.
3. **Mainnet (43114):** deploy a fresh `UsageAnchor` on C-Chain (the
   Fuji contract is not reused), switch `USAGE_ANCHOR_CHAIN_ID`,
   `USAGE_ANCHOR_RPC_URL` and `USAGE_ANCHOR_CONTRACT_ADDRESS`, then enable.
   `usage_anchors` rows are keyed by chain and contract, so Fuji history
   stays separate.
4. Backfill: the admin trigger anchors any finished day with usage.

## Wording rule

Public copy about this feature states facts only: "Gatewayz publishes a
daily Merkle root of its usage records on Avalanche C-Chain", with a link to
the contract on Snowtrace. Never claim or imply a partnership with,
endorsement by, or funding from Avalanche, Ava Labs or the Avalanche
Foundation.
