# WAYZ Staking On-Chain Indexer — Design

Status: draft, pending human review
Repo: Alpaca-Network/gatewayz-backend
Related: Alpaca-Network/gatewayz-backend#2244; contracts merged in
Alpaca-Network/gatewayz-protocol PR #1 (`WAYZToken.sol`, `WAYZStaking.sol`)

## Context

The WAYZ token pitch describes stake → daily inference capacity. The
contracts side of that loop (`WAYZStaking.sol`: `stake()`,
`requestUnstake()`, `cancelUnstake()`, `withdraw()`, `stakedBalanceOf()`,
`totalStaked()`) is built and merged. This document designs the backend
piece that turns on-chain stake state into a value the rest of the backend
can read: a fresh, queryable `wallet_stakes` table.

## Non-goals (explicitly out of scope for this design)

- **Enforcing** the allowance in `chat.py`'s credit/quota gate. That
  requires knowing which `user_id` a wallet belongs to, which is Epic 2
  (wallet-based auth, gatewayz-backend#2248-2254) — not built yet. This
  issue only keeps `wallet_stakes` fresh; a later issue wires it into
  request handling once wallet↔user linkage exists.
- Any change to `users`, `credit_transactions`, or the existing dollar-credit
  system.
- A persistent event-listener/websocket process. This is a poll-based job
  inside the existing FastAPI service, matching the
  `start_ledger_reconciliation_scheduler()` pattern in
  `src/services/scheduled_sync.py`.
- Batched/multicall RPC calls. At testnet scale (expected: a handful of
  stakers) one view call per wallet per sync run is fine. Flagged as a
  scaling limitation for later, not solved here.
- Picking the real `WAYZ_DAILY_INFERENCE_CAPACITY` value — that's a product
  decision. This design treats it as a configurable placeholder.

## Architecture

One new module, `src/services/chain/wayz_staking_sync.py`, using `web3.py`
(new dependency — no chain client exists anywhere in this repo today).
Scheduled via APScheduler, following the exact shape of
`start_ledger_reconciliation_scheduler()`.

### Sync algorithm (runs every `WAYZ_STAKING_SYNC_INTERVAL_MINUTES`)

The issue asks for two things — "index new stakes" and "reconcile missed
events" — but a poll loop that always re-reads authoritative on-chain state
does both by construction, so this design has one mechanism, not two:

1. Read the last-synced block from `chain_sync_cursors` (row keyed by
   `WAYZ_STAKING_CONTRACT_ADDRESS`; if no row exists, start from
   `WAYZ_STAKING_DEPLOY_BLOCK`).
2. `eth_getLogs` for the contract's `Staked` event topic, from
   `last_synced_block + 1` to the current chain head. For every log, take
   the staker address; if it's not already a row in `wallet_stakes`,
   insert one (`staked_amount = 0` placeholder — step 3 fills the real
   value in the same run).
   - Only `Staked` needs scanning here, not `UnstakeRequested`/
     `UnstakeCancelled`/`Withdrawn` — those never introduce a wallet this
     table hasn't seen (`WAYZStaking.sol` guarantees you can only unstake,
     cancel, or withdraw for a wallet that has already staked something at
     least once), so there is no discovery signal in them.
3. For every wallet currently in `wallet_stakes` (not just newly
   discovered ones), call `stakedBalanceOf(wallet)` — a plain view call,
   authoritative regardless of whether step 2 missed anything. Update
   `staked_amount`.
4. Call `totalStaked()` once. For every wallet, recompute
   `daily_allowance = floor(staked_amount * WAYZ_DAILY_INFERENCE_CAPACITY / totalStaked)`
   (integer math, `totalStaked == 0` guarded to avoid division by zero —
   set `daily_allowance = 0` for everyone in that case).
   Reference: `WAYZStaking.sol:stakedBalanceOf`/`totalStaked` are both
   `uint256` (18-decimal WAYZ, same as `WAYZToken.sol`'s `ether`-denominated
   mint amounts) — `daily_allowance` inherits that precision.
5. Write the batch (`wallet_stakes` upsert + `chain_sync_cursors` cursor
   advance) in one transaction. Set `last_synced_at = now()` on every
   touched row.

If any RPC call in steps 1-4 fails, log a warning
(`logger.warning("WAYZ staking sync failed (non-fatal): %s", e)`, matching
`ledger_reconciliation.py`'s convention) and leave the cursor unadvanced —
the next scheduled run retries from the same point. A sync failure must
never raise into the scheduler or crash the app.

### Startup / no-op behavior

Nothing is deployed to Fuji yet (only a local `anvil` dry-run has happened,
per the contracts repo's Task 11). `WAYZ_STAKING_CONTRACT_ADDRESS` will be
unset in every environment until a human runs the actual Fuji deployment.
`start_wayz_staking_sync_scheduler()` checks this at startup and returns
immediately (no job scheduled, just a log line) if the address is unset —
identical in shape to how `ENABLE_LEDGER_RECONCILIATION` gates its job, but
gated on contract-address presence rather than a boolean, since there's
nothing meaningful to toggle before a contract exists to point at.

## Data model

### `wallet_stakes`

| column | type | notes |
|---|---|---|
| `wallet_address` | `text` PK | Lowercased, checksum-agnostic (EVM addresses are case-insensitive; store lowercase, compare lowercase) |
| `staked_amount` | `numeric(78,0)` | Raw `uint256` wei-equivalent (18 decimals), from `stakedBalanceOf()` |
| `daily_allowance` | `numeric(78,0)` | Computed each sync run per the formula above |
| `last_synced_at` | `timestamptz` | Set on every successful sync touching this row |
| `last_synced_block` | `bigint` | The chain head at the time this row was last refreshed |
| `created_at` | `timestamptz default now()` | |

No RLS policy (service-role-only access, matching `credit_ledger`'s
pattern) — this table has no per-user-request read path yet since nothing
queries it outside the sync job (Non-goals).

### `chain_sync_cursors`

| column | type | notes |
|---|---|---|
| `contract_address` | `text` PK | Lowercased |
| `last_synced_block` | `bigint` | |
| `updated_at` | `timestamptz` | |

Generic on purpose (keyed by contract address, not named after WAYZ
specifically) so a future chain-indexed contract doesn't need its own
cursor table.

Migration file: `supabase/migrations/<timestamp>_wayz_staking_sync_tables.sql`,
following the repo's idempotent style (`CREATE TABLE IF NOT EXISTS`, a
`to_regclass` guard is not needed here since these are brand-new tables,
not ALTERs to existing ones).

## Config additions (`src/config/config.py`, `_get_env_var` pattern)

```python
AVALANCHE_FUJI_RPC_URL = _get_env_var(
    "AVALANCHE_FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc"
)
WAYZ_STAKING_CONTRACT_ADDRESS = _get_env_var("WAYZ_STAKING_CONTRACT_ADDRESS")
WAYZ_STAKING_DEPLOY_BLOCK = int(_get_env_var("WAYZ_STAKING_DEPLOY_BLOCK", "0"))
WAYZ_STAKING_SYNC_INTERVAL_MINUTES = int(
    _get_env_var("WAYZ_STAKING_SYNC_INTERVAL_MINUTES", "15")
)
WAYZ_DAILY_INFERENCE_CAPACITY = int(
    _get_env_var("WAYZ_DAILY_INFERENCE_CAPACITY", "0")
)  # Placeholder; real value is a product decision, not set here.
```

`WAYZ_STAKING_CONTRACT_ADDRESS` unset (the default, today) is exactly the
no-op condition from the Startup section above.

## Contract ABI

`gatewayz-backend` has no dependency on the `gatewayz-protocol` repo and
this design doesn't introduce one. A minimal ABI fragment covering only
what's called — `stakedBalanceOf(address) view returns (uint256)`,
`totalStaked() view returns (uint256)`, and the `Staked` event topic — is
vendored as a small JSON file,
`src/services/chain/abi/wayz_staking.json`, hand-written from
`WAYZStaking.sol`'s public interface rather than importing Foundry's full
build artifact.

## Error handling / edge cases

- **`totalStaked() == 0`**: every wallet's `daily_allowance` is set to `0`
  (no division by zero, no NaN).
- **RPC failure mid-run**: covered above — log and retry next run, cursor
  unadvanced, no partial writes (single transaction).
- **A wallet's `Staked` event log is missed** (RPC gap, restart during a
  sync window, etc.): harmless — the wallet simply isn't discovered until
  its *next* `Staked` event, or until any operator/support flow manually
  seeds a row. Since step 3 always re-reads live balance for every row
  already present, no *known* wallet's data ever goes stale beyond one
  sync interval; the only gap is discovery latency for a wallet's first
  stake.
- **RPC returns a reorg'd block** (head moves backward — rare on Avalanche's
  fast-finality consensus, but not impossible on a testnet): the next
  run's `eth_getLogs` range is `[last_synced_block+1, new_head]`; if
  `new_head < last_synced_block`, skip the log scan for that run (range is
  empty/invalid) but still run steps 3-4 (view calls are always current
  state, unaffected by which block they're read at just being "behind").

## Testing

- Unit tests for the sync algorithm against a mocked `web3.py` `Contract`
  object (no real RPC calls in tests) — cover: first-ever run (empty
  cursor, empty table), a run that discovers a new wallet, a run with
  `totalStaked() == 0`, an RPC exception mid-run (verify no partial write,
  verify cursor unchanged).
- No integration test against live Fuji RPC in this repo's test suite
  (matches existing provider-client test conventions — external network
  calls aren't exercised in CI).
- A migration smoke test (apply migration to a local Supabase instance,
  verify both tables exist with expected columns) if the repo's existing
  migration tests cover new-table migrations elsewhere; otherwise this is
  a manual verification step, not an automated test.

## Open questions for later (not blocking this spec)

- Real `WAYZ_DAILY_INFERENCE_CAPACITY` value.
- How `wallet_stakes` gets consumed once Epic 2 exists (a new column on
  `users`? A join at request time? A cache?) — deliberately undecided
  here since it depends on Epic 2's own design.
