# WAYZ Staking On-Chain Indexer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a `wallet_stakes` table fresh from the on-chain `WAYZStaking`
contract (Avalanche Fuji), per gatewayz-backend#2244.

**Architecture:** A poll-based APScheduler job (matching the existing
`start_ledger_reconciliation_scheduler()` pattern in `scheduled_sync.py`)
that scans `Staked` event logs to discover new stakers, then re-reads every
known wallet's live `stakedBalanceOf()` every run — doubling as
reconciliation, so there is no separate drift-repair mechanism.

**Tech Stack:** `web3.py` (new dependency), Supabase (raw SQL migration,
`supabase-py` client), APScheduler (already used in this repo).

**Spec:** `docs/superpowers/specs/2026-09-01-wayz-staking-indexer-design.md`

## Global Constraints

- No wiring into `chat.py`'s credit/quota gate — that needs Epic 2's
  wallet↔`user_id` linkage, which doesn't exist yet. This plan only keeps
  `wallet_stakes` fresh and queryable.
- Every DB-access function follows `src/db/routing_policies.py`'s
  convention exactly: wrap in try/except, `logger.warning` on failure,
  return a safe default (`None`/`[]`) — never raise. A sync job must never
  crash the app.
- Config vars follow `src/config/config.py`'s `_get_env_var` pattern.
  `WAYZ_STAKING_CONTRACT_ADDRESS` unset (true in every environment today —
  nothing is deployed to Fuji yet) means the scheduler no-ops at startup,
  logging once, not erroring.
- `staked_event_addresses(from_block, to_block)` returns `[]` (not an
  error) when `from_block > to_block` — this is the reorg/empty-range case
  from the spec, not exceptional.
- All new code lives under `src/services/chain/` (client + orchestration)
  and `src/db/wallet_stakes.py` (DB access), following the existing
  separation between provider-client modules and `src/db/*` modules.
- Tests mock `web3.py` and the Supabase client — no real RPC or DB calls in
  the test suite, matching `tests/db/test_routing_policies.py`'s
  MagicMock-based style exactly.

---

### Task 1: Migration — `wallet_stakes` and `chain_sync_cursors` tables

**Files:**
- Create: `supabase/migrations/20260901000000_wayz_staking_sync_tables.sql`

**Interfaces:**
- Produces: tables `public.wallet_stakes` (columns: `wallet_address` text PK,
  `staked_amount` numeric(78,0), `daily_allowance` numeric(78,0),
  `last_synced_block` bigint, `last_synced_at` timestamptz, `created_at`
  timestamptz) and `public.chain_sync_cursors` (columns: `contract_address`
  text PK, `last_synced_block` bigint, `updated_at` timestamptz).

- [ ] **Step 1: Write the migration**

Create `supabase/migrations/20260901000000_wayz_staking_sync_tables.sql`:

```sql
-- Migration: WAYZ staking on-chain indexer tables (gatewayz-backend#2244)
-- Created: 2026-09-01
-- Description:
--   Backing store for the poll-based WAYZStaking sync job
--   (src/services/chain/wayz_staking_sync.py). wallet_stakes holds the
--   latest known on-chain stake + computed daily allowance per wallet;
--   chain_sync_cursors tracks the last-synced block per contract so the
--   event-log scan can resume incrementally. Neither table is read by any
--   request-handling code yet (see spec's Non-goals) -- backend-only,
--   service_role access.

CREATE TABLE IF NOT EXISTS public.wallet_stakes (
    wallet_address     text PRIMARY KEY,
    staked_amount      numeric(78, 0) NOT NULL DEFAULT 0,
    daily_allowance    numeric(78, 0) NOT NULL DEFAULT 0,
    last_synced_block  bigint,
    last_synced_at     timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.chain_sync_cursors (
    contract_address   text PRIMARY KEY,
    last_synced_block  bigint NOT NULL,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Backend-only data, no per-user request path reads these yet: RLS on, no
-- permissive policy -> service_role only (mirrors credit_ledger's pattern).
ALTER TABLE public.wallet_stakes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chain_sync_cursors ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 2: Verify the migration applies cleanly**

Run: `supabase migration up` (or, if no local Supabase instance is running
in this environment, `supabase db push --dry-run` to validate syntax
without applying). If neither is available, at minimum run
`python3 -c "import sqlparse; sqlparse.parse(open('supabase/migrations/20260901000000_wayz_staking_sync_tables.sql').read())"`
or simply visually confirm the SQL is syntactically valid — do not skip
this step silently if the Supabase CLI isn't available; note in your report
which verification method you used.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260901000000_wayz_staking_sync_tables.sql
git commit -m "feat: add wallet_stakes and chain_sync_cursors tables"
```

---

### Task 2: `WayzStakingClient` — read-only web3.py wrapper

**Files:**
- Modify: `requirements.txt`
- Create: `src/services/chain/__init__.py`
- Create: `src/services/chain/abi/wayz_staking.json`
- Create: `src/services/chain/wayz_staking_client.py`
- Test: `tests/services/chain/test_wayz_staking_client.py`

**Interfaces:**
- Produces:
  - `class WayzStakingClientError(Exception)`
  - `class WayzStakingClient.__init__(self, rpc_url: str, contract_address: str)`
  - `WayzStakingClient.from_config() -> WayzStakingClient` (classmethod,
    raises `WayzStakingClientError` if `Config.WAYZ_STAKING_CONTRACT_ADDRESS`
    is unset)
  - `WayzStakingClient.current_block(self) -> int`
  - `WayzStakingClient.staked_balance_of(self, wallet_address: str) -> int`
  - `WayzStakingClient.total_staked(self) -> int`
  - `WayzStakingClient.staked_event_addresses(self, from_block: int, to_block: int) -> list[str]`
    (lowercased, deduplicated, sorted; `[]` if `from_block > to_block`)

- [ ] **Step 1: Add the `web3` dependency**

In `requirements.txt`, add a new line (alphabetical position doesn't matter
in this file — append near the bottom with the other non-core deps):

```
web3==7.6.0
```

If `web3==7.6.0` is unavailable at install time, install the latest
available `7.x` release instead and note the actual installed version in
your report — this pin is not load-bearing, the `7.x` snake_case API
(`to_checksum_address`, `w3.eth.block_number`,
`contract.events.X().get_logs(fromBlock=..., toBlock=...)`) is what
matters.

Run: `pip install -r requirements.txt` (or `pip install web3==7.6.0`
directly) and confirm `python3 -c "import web3; print(web3.__version__)"`
succeeds.

- [ ] **Step 2: Create the package and vendored ABI**

Create `src/services/chain/__init__.py` (empty file).

Create `src/services/chain/abi/wayz_staking.json` — hand-written minimal
fragment covering only what this client calls (not the full Foundry build
artifact; `gatewayz-backend` has no dependency on the `gatewayz-protocol`
repo):

```json
[
  {
    "type": "function",
    "name": "stakedBalanceOf",
    "stateMutability": "view",
    "inputs": [{"name": "", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}]
  },
  {
    "type": "function",
    "name": "totalStaked",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint256"}]
  },
  {
    "type": "event",
    "name": "Staked",
    "anonymous": false,
    "inputs": [
      {"name": "staker", "type": "address", "indexed": true},
      {"name": "amount", "type": "uint256", "indexed": false},
      {"name": "newTotalStaked", "type": "uint256", "indexed": false}
    ]
  }
]
```

This must match `WAYZStaking.sol`'s actual public interface
(`stakedBalanceOf`, `totalStaked`, `event Staked(address indexed staker,
uint256 amount, uint256 newTotalStaked)`) — see
`Alpaca-Network/gatewayz-protocol`'s `src/WAYZStaking.sol` if you need to
double check the merged contract.

- [ ] **Step 3: Write the failing tests**

Create `tests/services/chain/__init__.py` (empty file, if `tests/services/`
doesn't already treat subdirectories as packages — check for existing
`tests/services/*/​__init__.py` first and match whatever convention is
already there).

Create `tests/services/chain/test_wayz_staking_client.py`:

```python
"""Tests for src.services.chain.wayz_staking_client (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chain.wayz_staking_client import WayzStakingClient, WayzStakingClientError


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _make_client_with_mocked_web3():
    with patch("src.services.chain.wayz_staking_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        client = WayzStakingClient("http://fake-rpc", "0xcontract")
        return client, mock_w3, mock_contract


def test_current_block_reads_eth_block_number(sb):
    client, mock_w3, _ = _make_client_with_mocked_web3()
    mock_w3.eth.block_number = 12345
    assert client.current_block() == 12345


def test_staked_balance_of_calls_contract_function(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.functions.stakedBalanceOf.return_value.call.return_value = 500
    assert client.staked_balance_of("0xabc") == 500
    mock_contract.functions.stakedBalanceOf.assert_called_once_with("0xabc")


def test_total_staked_calls_contract_function(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.functions.totalStaked.return_value.call.return_value = 1000
    assert client.total_staked() == 1000


def test_staked_event_addresses_deduplicates_lowercases_and_sorts(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    mock_contract.events.Staked.return_value.get_logs.return_value = [
        {"args": {"staker": "0xABC"}},
        {"args": {"staker": "0xabc"}},
        {"args": {"staker": "0xDEF"}},
    ]
    result = client.staked_event_addresses(1, 100)
    assert result == ["0xabc", "0xdef"]
    mock_contract.events.Staked.return_value.get_logs.assert_called_once_with(
        fromBlock=1, toBlock=100
    )


def test_staked_event_addresses_returns_empty_for_invalid_range(sb):
    client, _, mock_contract = _make_client_with_mocked_web3()
    result = client.staked_event_addresses(100, 1)
    assert result == []
    mock_contract.events.Staked.return_value.get_logs.assert_not_called()


def test_from_config_raises_when_contract_address_unset(sb):
    with patch("src.services.chain.wayz_staking_client.Config") as mock_config:
        mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = None
        with pytest.raises(WayzStakingClientError):
            WayzStakingClient.from_config()


def test_from_config_builds_client_when_contract_address_set(sb):
    with patch("src.services.chain.wayz_staking_client.Config") as mock_config:
        mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
        mock_config.AVALANCHE_FUJI_RPC_URL = "http://fake-rpc"
        with patch("src.services.chain.wayz_staking_client.Web3") as mock_web3_cls:
            mock_web3_cls.return_value = MagicMock()
            mock_web3_cls.to_checksum_address.side_effect = lambda a: a
            client = WayzStakingClient.from_config()
            assert isinstance(client, WayzStakingClient)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/chain/test_wayz_staking_client.py -v`
Expected: FAIL — `src.services.chain.wayz_staking_client` module not found

- [ ] **Step 3: Write the implementation**

Create `src/services/chain/wayz_staking_client.py`:

```python
"""Read-only web3.py client for the WAYZStaking contract (Avalanche Fuji).

Wraps the on-chain view calls and event-log scan the sync job needs
(src/services/chain/wayz_staking_sync.py). Read-only -- never signs or
sends a transaction. See docs/superpowers/specs/2026-09-01-
wayz-staking-indexer-design.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

_ABI_PATH = Path(__file__).parent / "abi" / "wayz_staking.json"


class WayzStakingClientError(Exception):
    """Raised when the client can't be constructed (e.g. no contract configured)."""


def _load_abi() -> list[dict]:
    return json.loads(_ABI_PATH.read_text())


class WayzStakingClient:
    """Thin read-only wrapper around the deployed WAYZStaking contract."""

    def __init__(self, rpc_url: str, contract_address: str):
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=_load_abi(),
        )

    @classmethod
    def from_config(cls) -> "WayzStakingClient":
        if not Config.WAYZ_STAKING_CONTRACT_ADDRESS:
            raise WayzStakingClientError("WAYZ_STAKING_CONTRACT_ADDRESS is not set")
        return cls(Config.AVALANCHE_FUJI_RPC_URL, Config.WAYZ_STAKING_CONTRACT_ADDRESS)

    def current_block(self) -> int:
        return self._w3.eth.block_number

    def staked_balance_of(self, wallet_address: str) -> int:
        return self._contract.functions.stakedBalanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()

    def total_staked(self) -> int:
        return self._contract.functions.totalStaked().call()

    def staked_event_addresses(self, from_block: int, to_block: int) -> list[str]:
        """Distinct staker addresses from Staked events in [from_block, to_block].

        Returns [] (not an error) when from_block > to_block -- the caller's
        range can go empty/invalid after a reorg, and an empty scan is a
        valid, non-exceptional outcome.
        """
        if from_block > to_block:
            return []
        events = self._contract.events.Staked().get_logs(
            fromBlock=from_block, toBlock=to_block
        )
        return sorted({event["args"]["staker"].lower() for event in events})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/chain/test_wayz_staking_client.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt src/services/chain/__init__.py \
        src/services/chain/abi/wayz_staking.json \
        src/services/chain/wayz_staking_client.py \
        tests/services/chain/__init__.py \
        tests/services/chain/test_wayz_staking_client.py
git commit -m "feat: add WayzStakingClient (read-only web3.py wrapper)"
```

---

### Task 3: `src/db/wallet_stakes.py` — DB access module

**Files:**
- Create: `src/db/wallet_stakes.py`
- Test: `tests/db/test_wallet_stakes.py`

**Interfaces:**
- Consumes: `src.config.supabase_config.get_supabase_client` (existing).
- Produces:
  - `get_all_wallet_addresses() -> list[str]`
  - `insert_wallet_if_missing(wallet_address: str) -> None`
  - `upsert_wallet_stake(wallet_address: str, staked_amount: int, daily_allowance: int, last_synced_block: int, last_synced_at: str) -> None`
  - `get_sync_cursor(contract_address: str) -> int | None`
  - `set_sync_cursor(contract_address: str, block: int, updated_at: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_wallet_stakes.py`:

```python
"""Tests for src.db.wallet_stakes (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.wallet_stakes import (
    get_all_wallet_addresses,
    get_sync_cursor,
    insert_wallet_if_missing,
    set_sync_cursor,
    upsert_wallet_stake,
)


@pytest.fixture
def sb():
    return None


def _mock_table_client(table_data: dict):
    """table_data maps table name -> the .data a chained query call returns.

    Caches one query mock per table name so a later `client.table(name)`
    call in a test's assertions returns the SAME mock the function under
    test used -- not a fresh, uncalled one.
    """
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.upsert.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_get_all_wallet_addresses_returns_list(sb):
    client = _mock_table_client(
        {"wallet_stakes": [{"wallet_address": "0xabc"}, {"wallet_address": "0xdef"}]}
    )
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_all_wallet_addresses() == ["0xabc", "0xdef"]


def test_get_all_wallet_addresses_returns_empty_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_all_wallet_addresses() == []


def test_insert_wallet_if_missing_calls_upsert_with_ignore_duplicates(sb):
    client = _mock_table_client({})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        insert_wallet_if_missing("0xabc")

    table_query = client.table("wallet_stakes")
    table_query.upsert.assert_called_once()
    args, kwargs = table_query.upsert.call_args
    assert args[0]["wallet_address"] == "0xabc"
    assert kwargs["ignore_duplicates"] is True


def test_upsert_wallet_stake_writes_expected_row(sb):
    client = _mock_table_client({})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        upsert_wallet_stake("0xabc", 500, 10, 12345, "2026-09-01T00:00:00+00:00")

    table_query = client.table("wallet_stakes")
    args, kwargs = table_query.upsert.call_args
    assert args[0] == {
        "wallet_address": "0xabc",
        "staked_amount": "500",
        "daily_allowance": "10",
        "last_synced_block": 12345,
        "last_synced_at": "2026-09-01T00:00:00+00:00",
    }
    assert kwargs["on_conflict"] == "wallet_address"


def test_get_sync_cursor_returns_none_when_no_row(sb):
    client = _mock_table_client({"chain_sync_cursors": []})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") is None


def test_get_sync_cursor_returns_block_number(sb):
    client = _mock_table_client({"chain_sync_cursors": [{"last_synced_block": 999}]})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") == 999


def test_get_sync_cursor_returns_none_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        assert get_sync_cursor("0xcontract") is None


def test_set_sync_cursor_lowercases_contract_address(sb):
    client = _mock_table_client({})
    with patch("src.db.wallet_stakes.get_supabase_client", return_value=client):
        set_sync_cursor("0xCONTRACT", 999, "2026-09-01T00:00:00+00:00")

    table_query = client.table("chain_sync_cursors")
    args, kwargs = table_query.upsert.call_args
    assert args[0]["contract_address"] == "0xcontract"
    assert kwargs["on_conflict"] == "contract_address"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/db/test_wallet_stakes.py -v`
Expected: FAIL — `src.db.wallet_stakes` module not found

- [ ] **Step 3: Write the implementation**

Create `src/db/wallet_stakes.py`:

```python
"""DB access for wallet_stakes and chain_sync_cursors (gatewayz-backend#2244).

Backs the on-chain WAYZStaking indexer (src/services/chain/wayz_staking_sync.py).
Mirrors src/db/routing_policies.py's try/except + logger.warning + safe-default
convention exactly -- callers must treat a lookup failure as "no data," never
as a hard failure, since this backs a background sync job that must never
crash the app.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_WALLET_STAKES_TABLE = "wallet_stakes"
_CURSOR_TABLE = "chain_sync_cursors"


def get_all_wallet_addresses() -> list[str]:
    """All wallet addresses currently tracked. Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = client.table(_WALLET_STAKES_TABLE).select("wallet_address").execute()
        return [row["wallet_address"] for row in (result.data or [])]
    except Exception as e:
        logger.warning(f"wallet_stakes lookup failed: {e}")
        return []


def insert_wallet_if_missing(wallet_address: str) -> None:
    """Insert a new wallet_stakes row with zeroed balances if one doesn't exist.

    ignore_duplicates=True so a wallet discovered twice (same run or across
    overlapping runs) never errors and never clobbers an existing row's
    already-synced balance.
    """
    try:
        client = get_supabase_client()
        client.table(_WALLET_STAKES_TABLE).upsert(
            {
                "wallet_address": wallet_address,
                "staked_amount": "0",
                "daily_allowance": "0",
            },
            on_conflict="wallet_address",
            ignore_duplicates=True,
        ).execute()
    except Exception as e:
        logger.warning(f"wallet_stakes insert failed for {wallet_address}: {e}")


def upsert_wallet_stake(
    wallet_address: str,
    staked_amount: int,
    daily_allowance: int,
    last_synced_block: int,
    last_synced_at: str,
) -> None:
    """Write a wallet's freshly-synced staked amount and computed allowance."""
    try:
        client = get_supabase_client()
        client.table(_WALLET_STAKES_TABLE).upsert(
            {
                "wallet_address": wallet_address,
                "staked_amount": str(staked_amount),
                "daily_allowance": str(daily_allowance),
                "last_synced_block": last_synced_block,
                "last_synced_at": last_synced_at,
            },
            on_conflict="wallet_address",
        ).execute()
    except Exception as e:
        logger.warning(f"wallet_stakes upsert failed for {wallet_address}: {e}")


def get_sync_cursor(contract_address: str) -> int | None:
    """Last-synced block for this contract, or None if never synced (or on error)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CURSOR_TABLE)
            .select("last_synced_block")
            .eq("contract_address", contract_address.lower())
            .execute()
        )
        if not result.data:
            return None
        return int(result.data[0]["last_synced_block"])
    except Exception as e:
        logger.warning(f"chain_sync_cursors lookup failed: {e}")
        return None


def set_sync_cursor(contract_address: str, block: int, updated_at: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CURSOR_TABLE).upsert(
            {
                "contract_address": contract_address.lower(),
                "last_synced_block": block,
                "updated_at": updated_at,
            },
            on_conflict="contract_address",
        ).execute()
    except Exception as e:
        logger.warning(f"chain_sync_cursors update failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/db/test_wallet_stakes.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/db/wallet_stakes.py tests/db/test_wallet_stakes.py
git commit -m "feat: add wallet_stakes DB access module"
```

---

### Task 4: `wayz_staking_sync.py` — sync orchestration

**Files:**
- Create: `src/services/chain/wayz_staking_sync.py`
- Test: `tests/services/chain/test_wayz_staking_sync.py`

**Interfaces:**
- Consumes: `WayzStakingClient` (Task 2) — takes an already-constructed
  instance, does not build one itself (that's the caller's job via
  `WayzStakingClient.from_config()`). All 5 functions from
  `src.db.wallet_stakes` (Task 3). `Config` from `src.config.config`.
- Produces:
  - `@dataclass class SyncResult: wallets_discovered: int; wallets_synced: int; total_staked: int; from_block: int; to_block: int`
  - `sync_once(client: WayzStakingClient) -> SyncResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/services/chain/test_wayz_staking_sync.py`:

```python
"""Tests for src.services.chain.wayz_staking_sync (gatewayz-backend#2244)."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.chain.wayz_staking_sync import sync_once


@pytest.fixture
def sb():
    return None


def _mock_client(current_block, staked_balances: dict, total_staked, staked_events):
    client = MagicMock()
    client.current_block.return_value = current_block
    client.total_staked.return_value = total_staked
    client.staked_event_addresses.return_value = staked_events
    client.staked_balance_of.side_effect = lambda addr: staked_balances[addr]
    return client


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_first_run_scans_from_deploy_block_inclusive(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = None
    mock_get_all.return_value = []

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 500},
        total_staked=500, staked_events=["0xabc"],
    )

    result = sync_once(client)

    client.staked_event_addresses.assert_called_once_with(100, 200)
    mock_insert.assert_called_once_with("0xabc")
    mock_upsert.assert_called_once()
    upsert_args = mock_upsert.call_args[0]
    assert upsert_args[:4] == ("0xabc", 500, 1000, 200)
    assert isinstance(upsert_args[4], str) and upsert_args[4]  # ISO timestamp, non-empty
    assert result.wallets_discovered == 1
    assert result.wallets_synced == 1
    assert result.total_staked == 500
    assert result.from_block == 100
    assert result.to_block == 200
    mock_set_cursor.assert_called_once()
    cursor_args = mock_set_cursor.call_args[0]
    assert cursor_args[:2] == ("0xcontract", 200)
    assert isinstance(cursor_args[2], str) and cursor_args[2]


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_subsequent_run_scans_from_cursor_plus_one(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc"]

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 500},
        total_staked=500, staked_events=[],
    )

    result = sync_once(client)

    client.staked_event_addresses.assert_called_once_with(151, 200)
    mock_insert.assert_not_called()
    assert result.wallets_discovered == 0
    assert result.wallets_synced == 1
    assert result.from_block == 150


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_zero_total_staked_gives_zero_allowance(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc"]

    client = _mock_client(
        current_block=200, staked_balances={"0xabc": 0},
        total_staked=0, staked_events=[],
    )

    sync_once(client)

    args = mock_upsert.call_args[0]
    assert args[0] == "0xabc"
    assert args[1] == 0
    assert args[2] == 0  # daily_allowance


@patch("src.services.chain.wayz_staking_sync.Config")
@patch("src.services.chain.wayz_staking_sync.set_sync_cursor")
@patch("src.services.chain.wayz_staking_sync.upsert_wallet_stake")
@patch("src.services.chain.wayz_staking_sync.insert_wallet_if_missing")
@patch("src.services.chain.wayz_staking_sync.get_all_wallet_addresses")
@patch("src.services.chain.wayz_staking_sync.get_sync_cursor")
def test_known_wallet_is_always_resynced_even_with_no_new_events(
    mock_get_cursor, mock_get_all, mock_insert, mock_upsert, mock_set_cursor,
    mock_config, sb,
):
    """The always-re-read-every-known-wallet property is what makes this job
    double as reconciliation -- a wallet's balance is refreshed even when the
    event scan for this run found nothing."""
    mock_config.WAYZ_STAKING_CONTRACT_ADDRESS = "0xcontract"
    mock_config.WAYZ_STAKING_DEPLOY_BLOCK = 100
    mock_config.WAYZ_DAILY_INFERENCE_CAPACITY = 1000
    mock_get_cursor.return_value = 150
    mock_get_all.return_value = ["0xabc", "0xdef"]

    client = _mock_client(
        current_block=200,
        staked_balances={"0xabc": 300, "0xdef": 700},
        total_staked=1000,
        staked_events=[],
    )

    result = sync_once(client)

    assert result.wallets_synced == 2
    assert client.staked_balance_of.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/chain/test_wayz_staking_sync.py -v`
Expected: FAIL — `src.services.chain.wayz_staking_sync` module not found

- [ ] **Step 3: Write the implementation**

Create `src/services/chain/wayz_staking_sync.py`:

```python
"""WAYZ staking sync orchestration (gatewayz-backend#2244).

Every run: discover new stakers from Staked event logs, then re-read EVERY
known wallet's live on-chain balance -- this doubles as reconciliation (see
docs/superpowers/specs/2026-09-01-wayz-staking-indexer-design.md), so there
is no separate "drift repair" pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from src.config.config import Config
from src.db.wallet_stakes import (
    get_all_wallet_addresses,
    get_sync_cursor,
    insert_wallet_if_missing,
    set_sync_cursor,
    upsert_wallet_stake,
)
from src.services.chain.wayz_staking_client import WayzStakingClient

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    wallets_discovered: int
    wallets_synced: int
    total_staked: int
    from_block: int
    to_block: int


def sync_once(client: WayzStakingClient) -> SyncResult:
    """Run one full sync pass against an already-constructed client.

    Building the client (and deciding whether to run at all) is the
    caller's job -- see WayzStakingClient.from_config() and the scheduled
    job in scheduled_sync.py (Task 5), which catches WayzStakingClientError
    separately from unexpected failures.
    """
    contract_address = Config.WAYZ_STAKING_CONTRACT_ADDRESS

    cursor = get_sync_cursor(contract_address)
    from_block = cursor if cursor is not None else Config.WAYZ_STAKING_DEPLOY_BLOCK
    to_block = client.current_block()
    scan_start = from_block if cursor is None else from_block + 1

    new_addresses = client.staked_event_addresses(scan_start, to_block)
    known_addresses = set(get_all_wallet_addresses())
    discovered = [addr for addr in new_addresses if addr not in known_addresses]
    for addr in discovered:
        insert_wallet_if_missing(addr)

    all_addresses = known_addresses | set(discovered)
    total_staked = client.total_staked()

    now = datetime.now(UTC).isoformat()
    synced = 0
    for addr in all_addresses:
        staked = client.staked_balance_of(addr)
        allowance = (
            0
            if total_staked == 0
            else (staked * Config.WAYZ_DAILY_INFERENCE_CAPACITY) // total_staked
        )
        upsert_wallet_stake(addr, staked, allowance, to_block, now)
        synced += 1

    set_sync_cursor(contract_address, to_block, now)

    return SyncResult(
        wallets_discovered=len(discovered),
        wallets_synced=synced,
        total_staked=total_staked,
        from_block=from_block,
        to_block=to_block,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/chain/test_wayz_staking_sync.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/chain/wayz_staking_sync.py tests/services/chain/test_wayz_staking_sync.py
git commit -m "feat: add wayz_staking_sync orchestration (sync_once)"
```

---

### Task 5: Config, scheduler wiring, startup wiring

**Files:**
- Modify: `src/config/config.py`
- Modify: `src/services/scheduled_sync.py`
- Modify: `src/services/startup.py`

**Interfaces:**
- Consumes: `sync_once` and `SyncResult` (Task 4), `WayzStakingClient` /
  `WayzStakingClientError` (Task 2).
- Produces:
  - `Config.AVALANCHE_FUJI_RPC_URL`, `Config.WAYZ_STAKING_CONTRACT_ADDRESS`,
    `Config.WAYZ_STAKING_DEPLOY_BLOCK`,
    `Config.WAYZ_STAKING_SYNC_INTERVAL_MINUTES`,
    `Config.WAYZ_DAILY_INFERENCE_CAPACITY`
  - `run_scheduled_wayz_staking_sync()` (async, in `scheduled_sync.py`)
  - `start_wayz_staking_sync_scheduler()` / `stop_wayz_staking_sync_scheduler()`
    (in `scheduled_sync.py`)

- [ ] **Step 1: Add config vars**

In `src/config/config.py`, add near the existing
`ENABLE_LEDGER_RECONCILIATION` / `LEDGER_RECONCILIATION_*` block (same
`class Config:` body, same `_get_env_var`/`os.environ.get` conventions
already used there):

```python
    # WAYZ staking on-chain sync (gatewayz-backend#2244). Unset contract
    # address (the default -- nothing is deployed to Fuji yet) means the
    # scheduler no-ops at startup rather than erroring.
    AVALANCHE_FUJI_RPC_URL = _get_env_var(
        "AVALANCHE_FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc"
    )
    WAYZ_STAKING_CONTRACT_ADDRESS = _get_env_var("WAYZ_STAKING_CONTRACT_ADDRESS")
    WAYZ_STAKING_DEPLOY_BLOCK = int(_get_env_var("WAYZ_STAKING_DEPLOY_BLOCK", "0"))
    WAYZ_STAKING_SYNC_INTERVAL_MINUTES = int(
        _get_env_var("WAYZ_STAKING_SYNC_INTERVAL_MINUTES", "15")
    )
    # Placeholder; the real value is a product decision, not set here.
    WAYZ_DAILY_INFERENCE_CAPACITY = int(
        _get_env_var("WAYZ_DAILY_INFERENCE_CAPACITY", "0")
    )
```

- [ ] **Step 2: Add the scheduled job + start/stop functions to `scheduled_sync.py`**

Near the existing `_recon_scheduler` global and
`start_ledger_reconciliation_scheduler()`/`stop_ledger_reconciliation_scheduler()`
functions (same file, same section style — a short comment banner above
the block, matching the existing `# ====...====` banners in this file),
add:

```python
_wayz_staking_scheduler: AsyncIOScheduler | None = None
_last_wayz_staking_sync_status: dict[str, Any] = {
    "last_run_time": None,
    "last_ok": None,
    "wallets_synced": None,
}


async def run_scheduled_wayz_staking_sync():
    """Sync wallet_stakes from the on-chain WAYZStaking contract."""
    from src.services.chain.wayz_staking_client import (
        WayzStakingClient,
        WayzStakingClientError,
    )
    from src.services.chain.wayz_staking_sync import sync_once

    _last_wayz_staking_sync_status["last_run_time"] = datetime.now(UTC)
    try:
        client = WayzStakingClient.from_config()
        result = await asyncio.to_thread(sync_once, client)
        _last_wayz_staking_sync_status["last_ok"] = True
        _last_wayz_staking_sync_status["wallets_synced"] = result.wallets_synced
        logger.info(
            "✅ WAYZ staking sync OK | discovered=%s synced=%s total_staked=%s blocks=%s..%s",
            result.wallets_discovered,
            result.wallets_synced,
            result.total_staked,
            result.from_block,
            result.to_block,
        )
    except WayzStakingClientError as e:
        logger.info("WAYZ staking sync skipped: %s", e)
    except Exception as e:
        _last_wayz_staking_sync_status["last_ok"] = False
        logger.warning("WAYZ staking sync failed (non-fatal): %s", e)


def start_wayz_staking_sync_scheduler():
    """Start the APScheduler for WAYZ staking sync (app lifespan)."""
    global _wayz_staking_scheduler

    if not Config.WAYZ_STAKING_CONTRACT_ADDRESS:
        logger.info("WAYZ staking sync DISABLED: WAYZ_STAKING_CONTRACT_ADDRESS not set")
        return

    interval_minutes = Config.WAYZ_STAKING_SYNC_INTERVAL_MINUTES
    logger.info("Starting WAYZ staking sync scheduler (interval: %s min)", interval_minutes)
    try:
        _wayz_staking_scheduler = AsyncIOScheduler()
        _wayz_staking_scheduler.add_job(
            run_scheduled_wayz_staking_sync,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="wayz_staking_sync",
            name="WAYZ Staking On-Chain Sync Job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _wayz_staking_scheduler.start()
        logger.info(
            "✅ WAYZ staking sync scheduler started (next run in %s min)", interval_minutes
        )
    except Exception as e:
        logger.error("❌ Failed to start WAYZ staking sync scheduler: %s", e)
        logger.exception(e)


def stop_wayz_staking_sync_scheduler():
    """Stop the WAYZ staking sync APScheduler gracefully (called during shutdown)."""
    global _wayz_staking_scheduler

    if _wayz_staking_scheduler is None:
        return
    logger.info("Stopping WAYZ staking sync scheduler...")
    try:
        _wayz_staking_scheduler.shutdown(wait=True)
        logger.info("✅ WAYZ staking sync scheduler stopped successfully")
    except Exception as e:
        logger.error("❌ Error stopping WAYZ staking sync scheduler: %s", e)
    finally:
        _wayz_staking_scheduler = None
```

Every name used here (`AsyncIOScheduler`, `IntervalTrigger`, `Config`,
`datetime`, `UTC`, `asyncio`, `logger`) is already imported at the top of
`scheduled_sync.py` — no new imports needed in this file.

- [ ] **Step 3: Wire into `startup.py`**

In `src/services/startup.py`, immediately after the existing block that
calls `start_ledger_reconciliation_scheduler()` (around line 636-642),
add a matching block:

```python
    # Start WAYZ staking on-chain sync (gatewayz-backend#2244)
    try:
        from src.services.scheduled_sync import start_wayz_staking_sync_scheduler

        start_wayz_staking_sync_scheduler()
        logger.info("WAYZ staking sync service initialized")
    except Exception as e:
        logger.warning(f"Failed to start WAYZ staking sync scheduler: {e}")
        # Don't fail startup if WAYZ staking sync fails to start
```

And in the shutdown section, immediately after the existing
`stop_ledger_reconciliation_scheduler()` call (around line 793-795), add:

```python
        from src.services.scheduled_sync import stop_wayz_staking_sync_scheduler

        stop_wayz_staking_sync_scheduler()
```

Match the exact surrounding try/except structure already used for the
ledger-reconciliation stop call at that location — read the ~10 lines
around line 793 before editing to mirror it precisely (whether it shares
a single try/except with neighboring stop calls or has its own).

- [ ] **Step 4: Verify nothing broke**

Run: `python3 -c "from src.services import scheduled_sync, startup"` — must
import cleanly with no syntax/import errors.

Run: `pytest tests/services/chain/ tests/db/test_wallet_stakes.py -v` —
all tests from Tasks 2-4 must still pass (this task doesn't add new tests
of its own; it's wiring existing, already-tested pieces together).

- [ ] **Step 5: Commit**

```bash
git add src/config/config.py src/services/scheduled_sync.py src/services/startup.py
git commit -m "feat: wire WAYZ staking sync into scheduler and startup"
```

---

## Self-Review Notes

- **Spec coverage:** Poll-based sync via event-discovery + always-re-read
  ✓ (Task 4), `wallet_stakes`/`chain_sync_cursors` schema ✓ (Task 1),
  config additions ✓ (Task 5), no-op when contract address unset ✓
  (Task 5's `start_wayz_staking_sync_scheduler`), vendored minimal ABI ✓
  (Task 2), `totalStaked() == 0` guard ✓ (Task 4), reorg/empty-range guard
  ✓ (Task 2's `staked_event_addresses`). Explicitly out of scope per the
  spec's Non-goals (no `chat.py` wiring, no wallet↔user linkage, no
  multicall batching) — no tasks needed for them.
- **Placeholder scan:** none found — every step has runnable code or an
  exact command. `WAYZ_DAILY_INFERENCE_CAPACITY` defaults to `0`
  intentionally (spec's stated placeholder, not a plan gap).
- **Type consistency:** `SyncResult`'s 5 fields are used identically in
  Task 4's implementation and Task 5's logging call. The 5-function
  `src.db.wallet_stakes` interface (Task 3) is imported with identical
  names in Task 4. `WayzStakingClient`'s 4 methods (Task 2) are called
  with identical signatures in Task 4's `sync_once` and its tests.
