# WAYZ Testnet Faucet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an existing gatewayz-backend account claim testnet WAYZ once,
proving wallet ownership via a signed message, per gatewayz-backend#2245.

**Architecture:** Two new authenticated endpoints (`POST /faucet/nonce`,
`POST /faucet/claim`) backed by a new `faucet_claims` table (unique per
user and per wallet) and a new signing-capable web3.py client
(`WayzTokenFaucetClient`) separate from the indexer's read-only client.

**Tech Stack:** `eth_account` (already a transitive dependency of `web3`,
already in `requirements.txt` via gatewayz-backend#2244), FastAPI,
Supabase, Redis (nonce storage).

**Spec:** `docs/superpowers/specs/2026-09-01-wayz-testnet-faucet-design.md`

## Global Constraints

- Eligibility: `usage_records` has ≥1 row for the caller's `user_id`
  (`Config.WAYZ_FAUCET_MIN_REQUESTS`, default `1`). No Genesis Points
  system — confirmed not to exist anywhere in this repo.
- One claim per `user_id` AND per `wallet_address`, enforced by two
  separate `UNIQUE` constraints on `faucet_claims` — the DB is the
  race-safety mechanism, not application-level locking.
- Wallet ownership is proven per-request via a signed message
  (`eth_account.Account.recover_message` + `encode_defunct`), never
  stored as a persistent link (that's Epic 2's job, not built).
- `WAYZ_TOKEN_CONTRACT_ADDRESS` / `WAYZ_FAUCET_MINTER_PRIVATE_KEY` unset
  (true today) → `/faucet/claim` returns 503, not a crash.
- Claim amount: `Config.WAYZ_FAUCET_CLAIM_AMOUNT` (default `1000`, whole
  WAYZ — converted to wei via `* 10**18` at mint time).
- `WayzTokenFaucetClient` is a SEPARATE module from
  `src/services/chain/wayz_staking_client.py` — different trust tier
  (holds a live signing key). Do not add minting to the read-only client.
- Before writing any web3.py/eth_account call whose exact kwarg/attribute
  names you're not 100% sure of, verify against the installed library
  with `python3 -c "..."` first — gatewayz-backend#2244 shipped a
  `get_logs(fromBlock=...)` bug that passed every mocked test and reached
  production before an independent review caught it. This plan's own
  code below was verified this way already (`build_transaction`,
  `sign_transaction().raw_transaction`, `send_raw_transaction`,
  `get_transaction_count` all confirmed against the installed
  `web3`/`eth_account` versions) — if you change any of it, re-verify.

---

### Task 1: Migration — `faucet_claims` table

**Files:**
- Create: `supabase/migrations/20260901010000_wayz_faucet_claims.sql`

**Interfaces:**
- Produces: table `public.faucet_claims` (`id` bigint PK, `user_id` bigint
  UNIQUE, `wallet_address` text UNIQUE, `amount` numeric(78,0), `status`
  text CHECK, `tx_hash` text, `error` text, `claimed_at` timestamptz).

- [ ] **Step 1: Write the migration**

Create `supabase/migrations/20260901010000_wayz_faucet_claims.sql`:

```sql
-- Migration: WAYZ testnet faucet claims table (gatewayz-backend#2245)
-- Created: 2026-09-01
-- Description:
--   One row per successful/attempted faucet claim. UNIQUE on both
--   user_id and wallet_address enforces "one claim per account AND per
--   wallet" -- the actual anti-sybil mechanism (rate limiting on the
--   endpoints is a coarse abuse guard on top of this, not the primary
--   defense). No RLS policy -- service-role only, nothing reads this
--   from a per-user request path.

CREATE TABLE IF NOT EXISTS public.faucet_claims (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint NOT NULL UNIQUE,
    wallet_address  text NOT NULL UNIQUE,
    amount          numeric(78, 0) NOT NULL,
    status          text NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    tx_hash         text,
    error           text,
    claimed_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.faucet_claims ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 2: Verify the migration is syntactically valid**

Run the same verification method used for gatewayz-backend#2244's
migration (Supabase CLI if available in this environment, otherwise
visual confirmation) — note which method in your report.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260901010000_wayz_faucet_claims.sql
git commit -m "feat: add faucet_claims table"
```

---

### Task 2: Config additions

**Files:**
- Modify: `src/config/config.py`

**Interfaces:**
- Produces: `Config.WAYZ_TOKEN_CONTRACT_ADDRESS`,
  `Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY`,
  `Config.WAYZ_FAUCET_CLAIM_AMOUNT`, `Config.WAYZ_FAUCET_MIN_REQUESTS`.

- [ ] **Step 1: Add the config vars**

Near the existing `WAYZ_STAKING_*` block added for gatewayz-backend#2244
(same `class Config:` body, same `_get_env_var` conventions), add:

```python
    # WAYZ testnet faucet (gatewayz-backend#2245). Unset contract address
    # or minter key (the default -- nothing is deployed to Fuji yet, no
    # minter key provisioned) means /faucet/claim returns 503.
    WAYZ_TOKEN_CONTRACT_ADDRESS = _get_env_var("WAYZ_TOKEN_CONTRACT_ADDRESS")
    WAYZ_FAUCET_MINTER_PRIVATE_KEY = _get_env_var("WAYZ_FAUCET_MINTER_PRIVATE_KEY")
    WAYZ_FAUCET_CLAIM_AMOUNT = int(_get_env_var("WAYZ_FAUCET_CLAIM_AMOUNT", "1000"))
    WAYZ_FAUCET_MIN_REQUESTS = int(_get_env_var("WAYZ_FAUCET_MIN_REQUESTS", "1"))
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python3 -c "from src.config.config import Config; print(Config.WAYZ_FAUCET_CLAIM_AMOUNT)"`
Expected: `1000`

- [ ] **Step 3: Commit**

```bash
git add src/config/config.py
git commit -m "feat: add WAYZ faucet config vars"
```

---

### Task 3: `WayzTokenFaucetClient` — signing-capable minting client

**Files:**
- Create: `src/services/chain/abi/wayz_token.json`
- Create: `src/services/chain/wayz_token_faucet_client.py`
- Test: `tests/services/chain/test_wayz_token_faucet_client.py`

**Interfaces:**
- Produces:
  - `class WayzTokenFaucetClientError(Exception)`
  - `class WayzTokenFaucetClient.__init__(self, rpc_url: str, contract_address: str, private_key: str)`
  - `WayzTokenFaucetClient.from_config() -> WayzTokenFaucetClient`
    (raises `WayzTokenFaucetClientError` if either
    `Config.WAYZ_TOKEN_CONTRACT_ADDRESS` or
    `Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY` is unset)
  - `async WayzTokenFaucetClient.mint(self, to_address: str, amount_wayz: int) -> str`
    (returns the tx hash as a `0x`-prefixed hex string)

- [ ] **Step 1: Write the vendored ABI**

Create `src/services/chain/abi/wayz_token.json` — minimal fragment
covering only `mint`, since that's the only function this client calls
(separate from any future need to read `WAYZToken`'s other view
functions):

```json
[
  {
    "type": "function",
    "name": "mint",
    "stateMutability": "nonpayable",
    "inputs": [
      {"name": "to", "type": "address"},
      {"name": "amount", "type": "uint256"}
    ],
    "outputs": []
  }
]
```

- [ ] **Step 2: Write the failing tests**

Create `tests/services/chain/test_wayz_token_faucet_client.py`:

```python
"""Tests for src.services.chain.wayz_token_faucet_client (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from src.services.chain.wayz_token_faucet_client import (
    WayzTokenFaucetClient,
    WayzTokenFaucetClientError,
)


@pytest.fixture
def sb():
    """No-op fixture whose mere presence bypasses the autouse DB-skip in
    tests/conftest.py -- this is a pure unit test with everything mocked."""
    return None


def _make_client_with_mocked_web3(private_key: str):
    with patch("src.services.chain.wayz_token_faucet_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_contract = MagicMock()
        mock_w3.eth.contract.return_value = mock_contract
        client = WayzTokenFaucetClient("http://fake-rpc", "0xcontract", private_key)
        return client, mock_w3, mock_contract


def test_from_config_raises_when_contract_address_unset(sb):
    with patch("src.services.chain.wayz_token_faucet_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = None
        mock_config.WAYZ_FAUCET_MINTER_PRIVATE_KEY = "0x" + "1" * 64
        with pytest.raises(WayzTokenFaucetClientError):
            WayzTokenFaucetClient.from_config()


def test_from_config_raises_when_minter_key_unset(sb):
    with patch("src.services.chain.wayz_token_faucet_client.Config") as mock_config:
        mock_config.WAYZ_TOKEN_CONTRACT_ADDRESS = "0xcontract"
        mock_config.WAYZ_FAUCET_MINTER_PRIVATE_KEY = None
        with pytest.raises(WayzTokenFaucetClientError):
            WayzTokenFaucetClient.from_config()


@pytest.mark.asyncio
async def test_mint_builds_signs_and_sends_transaction(sb):
    # A real, throwaway test private key -- not a secret, generated fresh
    # each run via Account.create(), used only to prove the client's
    # build/sign/send call chain against REAL eth_account signing (not a
    # mocked one) -- signing is the security-critical step to prove works
    # for real, matching the lesson from gatewayz-backend#2244's
    # get_logs bug (a fully-mocked call proves nothing about real usage).
    test_account = Account.create()
    client, mock_w3, mock_contract = _make_client_with_mocked_web3(test_account.key.hex())

    mock_w3.eth.get_transaction_count.return_value = 5
    mock_w3.eth.chain_id = 43113
    mock_w3.eth.gas_price = 1_000_000_000
    mock_contract.functions.mint.return_value.build_transaction.return_value = {
        "from": test_account.address,
        "nonce": 5,
        "chainId": 43113,
        "gas": 200_000,
        "gasPrice": 1_000_000_000,
        "to": "0xcontract",
        "value": 0,
        "data": "0xdeadbeef",
    }
    mock_w3.eth.send_raw_transaction.return_value = MagicMock(
        hex=lambda: "0xabc123"
    )

    tx_hash = await client.mint("0xrecipient", 1000)

    assert tx_hash == "0xabc123"
    mock_contract.functions.mint.assert_called_once_with("0xrecipient", 1000 * 10**18)
    mock_w3.eth.send_raw_transaction.assert_called_once()


def test_real_eth_account_sign_transaction_uses_raw_transaction_attribute(sb):
    """Regression guard: eth_account's SignedTransaction exposes
    `raw_transaction` (snake_case) in the installed version, not
    `rawTransaction`. Verified directly against a real (unmocked)
    Account.sign_transaction call -- if a future eth_account upgrade
    renames this attribute, this test catches it before the real
    mint() code path (which reads client._mint_sync's use of the same
    attribute) does."""
    account = Account.create()
    tx = {
        "to": "0x0000000000000000000000000000000000000001",
        "value": 0,
        "gas": 21000,
        "gasPrice": 1_000_000_000,
        "nonce": 0,
        "chainId": 43113,
    }
    signed = account.sign_transaction(tx)
    assert hasattr(signed, "raw_transaction")
    assert isinstance(signed.raw_transaction, (bytes, bytearray))
```

Note: this test file uses `@pytest.mark.asyncio` — check whether
`pytest-asyncio` is already installed/configured in this repo (look for
`asyncio_mode` in `pytest.ini`/`pyproject.toml`, or existing
`@pytest.mark.asyncio` usage elsewhere in `tests/`) before assuming it
works out of the box; if it's not configured, add the marker
registration or use `asyncio_mode = "auto"` per whatever the repo's
existing async test convention is — check
`tests/services/test_email_verification.py` or similar async-service
tests for the established pattern first.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/services/chain/test_wayz_token_faucet_client.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Write the implementation**

Create `src/services/chain/wayz_token_faucet_client.py`:

```python
"""Signing-capable web3.py client for minting testnet WAYZ (gatewayz-backend#2245).

Separate from src/services/chain/wayz_staking_client.py (read-only) --
this module holds a live MINTER_ROLE private key and signs transactions,
a different trust tier. See docs/superpowers/specs/2026-09-01-
wayz-testnet-faucet-design.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from eth_account import Account
from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

_ABI_PATH = Path(__file__).parent / "abi" / "wayz_token.json"
_WAYZ_DECIMALS = 18
_MINT_GAS_LIMIT = 200_000

_mint_lock = asyncio.Lock()


class WayzTokenFaucetClientError(Exception):
    """Raised when the client can't be constructed (e.g. faucet not configured)."""


def _load_abi() -> list[dict]:
    return json.loads(_ABI_PATH.read_text())


class WayzTokenFaucetClient:
    """Signs and sends mint() transactions against the deployed WAYZToken contract."""

    def __init__(self, rpc_url: str, contract_address: str, private_key: str):
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=_load_abi(),
        )

    @classmethod
    def from_config(cls) -> "WayzTokenFaucetClient":
        if not Config.WAYZ_TOKEN_CONTRACT_ADDRESS or not Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY:
            raise WayzTokenFaucetClientError(
                "WAYZ_TOKEN_CONTRACT_ADDRESS or WAYZ_FAUCET_MINTER_PRIVATE_KEY is not set"
            )
        return cls(
            Config.AVALANCHE_FUJI_RPC_URL,
            Config.WAYZ_TOKEN_CONTRACT_ADDRESS,
            Config.WAYZ_FAUCET_MINTER_PRIVATE_KEY,
        )

    async def mint(self, to_address: str, amount_wayz: int) -> str:
        """Mint amount_wayz whole WAYZ to to_address. Returns the tx hash.

        Serialized by a module-level asyncio.Lock -- the minter account's
        on-chain transaction nonce would race under concurrent claims
        otherwise.
        """
        async with _mint_lock:
            return await asyncio.to_thread(self._mint_sync, to_address, amount_wayz)

    def _mint_sync(self, to_address: str, amount_wayz: int) -> str:
        checksum_to = Web3.to_checksum_address(to_address)
        amount_wei = amount_wayz * (10**_WAYZ_DECIMALS)
        nonce = self._w3.eth.get_transaction_count(self._account.address, "pending")
        tx = self._contract.functions.mint(checksum_to, amount_wei).build_transaction(
            {
                "from": self._account.address,
                "nonce": nonce,
                "chainId": self._w3.eth.chain_id,
                "gas": _MINT_GAS_LIMIT,
                "gasPrice": self._w3.eth.gas_price,
            }
        )
        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/services/chain/test_wayz_token_faucet_client.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/services/chain/abi/wayz_token.json \
        src/services/chain/wayz_token_faucet_client.py \
        tests/services/chain/test_wayz_token_faucet_client.py
git commit -m "feat: add WayzTokenFaucetClient (signing-capable mint client)"
```

---

### Task 4: `src/db/faucet.py` — eligibility + claim CRUD

**Files:**
- Create: `src/db/faucet.py`
- Test: `tests/db/test_faucet.py`

**Interfaces:**
- Consumes: `src.config.supabase_config.get_supabase_client` (existing).
- Produces:
  - `has_completed_at_least_one_request(user_id: int, min_requests: int = 1) -> bool`
  - `get_existing_claim(user_id: int, wallet_address: str) -> dict | None`
    (returns a row if either `user_id` or `wallet_address` already has a
    claim)
  - `create_pending_claim(user_id: int, wallet_address: str, amount: int) -> dict | None`
    (returns the inserted row, or `None` if the insert failed — e.g. a
    unique-constraint violation from a race; caller treats `None` as
    "already claimed")
  - `mark_claim_sent(claim_id: int, tx_hash: str) -> None`
  - `mark_claim_failed(claim_id: int, error: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_faucet.py`:

```python
"""Tests for src.db.faucet (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.faucet import (
    create_pending_claim,
    get_existing_claim,
    has_completed_at_least_one_request,
    mark_claim_failed,
    mark_claim_sent,
)


@pytest.fixture
def sb():
    return None


def _mock_table_client(table_data: dict, raise_on_insert: bool = False):
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            query.select.return_value = query
            query.eq.return_value = query
            query.or_.return_value = query
            query.update.return_value = query
            if raise_on_insert and name == "faucet_claims":
                query.insert.side_effect = RuntimeError("duplicate key value")
            else:
                query.insert.return_value = query
            query.execute.return_value = MagicMock(data=table_data.get(name, []))
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def test_has_completed_at_least_one_request_true(sb):
    client = _mock_table_client({"usage_records": [{"id": 1}]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is True


def test_has_completed_at_least_one_request_false(sb):
    client = _mock_table_client({"usage_records": []})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is False


def test_has_completed_at_least_one_request_false_on_error(sb):
    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert has_completed_at_least_one_request(42) is False


def test_get_existing_claim_returns_none_when_no_row(sb):
    client = _mock_table_client({"faucet_claims": []})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert get_existing_claim(42, "0xabc") is None


def test_get_existing_claim_returns_row_when_present(sb):
    row = {"id": 1, "user_id": 42, "wallet_address": "0xabc", "status": "sent"}
    client = _mock_table_client({"faucet_claims": [row]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        assert get_existing_claim(42, "0xabc") == row


def test_create_pending_claim_returns_row_on_success(sb):
    inserted = {"id": 7, "user_id": 42, "wallet_address": "0xabc", "status": "pending"}
    client = _mock_table_client({"faucet_claims": [inserted]})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        result = create_pending_claim(42, "0xabc", 1000)
    assert result == inserted


def test_create_pending_claim_returns_none_on_unique_violation(sb):
    client = _mock_table_client({}, raise_on_insert=True)
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        result = create_pending_claim(42, "0xabc", 1000)
    assert result is None


def test_mark_claim_sent_updates_status_and_tx_hash(sb):
    client = _mock_table_client({})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        mark_claim_sent(7, "0xtxhash")

    table_query = client.table("faucet_claims")
    args, kwargs = table_query.update.call_args
    assert args[0] == {"status": "sent", "tx_hash": "0xtxhash"}


def test_mark_claim_failed_updates_status_and_error(sb):
    client = _mock_table_client({})
    with patch("src.db.faucet.get_supabase_client", return_value=client):
        mark_claim_failed(7, "insufficient funds")

    table_query = client.table("faucet_claims")
    args, kwargs = table_query.update.call_args
    assert args[0] == {"status": "failed", "error": "insufficient funds"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/db/test_faucet.py -v`
Expected: FAIL — `src.db.faucet` module not found

- [ ] **Step 3: Write the implementation**

Create `src/db/faucet.py`:

```python
"""DB access for faucet_claims and eligibility checks (gatewayz-backend#2245).

Mirrors src/db/routing_policies.py's try/except + logger.warning +
safe-default convention for reads. create_pending_claim is the exception:
a failed insert (including a unique-constraint violation, the expected
race outcome of a duplicate claim) returns None rather than raising --
callers treat None as "already claimed," not as a hard failure.
"""

from __future__ import annotations

import logging

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_CLAIMS_TABLE = "faucet_claims"
_USAGE_TABLE = "usage_records"


def has_completed_at_least_one_request(user_id: int, min_requests: int = 1) -> bool:
    """True if this user has at least min_requests rows in usage_records."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_USAGE_TABLE)
            .select("id")
            .eq("user_id", user_id)
            .execute()
        )
        return len(result.data or []) >= min_requests
    except Exception as e:
        logger.warning(f"usage_records eligibility check failed for user {user_id}: {e}")
        return False


def get_existing_claim(user_id: int, wallet_address: str) -> dict | None:
    """A faucet_claims row for this user OR this wallet, if either already claimed."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CLAIMS_TABLE)
            .select("*")
            .or_(f"user_id.eq.{user_id},wallet_address.eq.{wallet_address}")
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"faucet_claims lookup failed for user {user_id}: {e}")
        return None


def create_pending_claim(user_id: int, wallet_address: str, amount: int) -> dict | None:
    """Insert a pending claim row. Returns the row, or None on any failure
    (including the expected unique-constraint violation from a duplicate
    claim -- the caller treats None as "already claimed")."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_CLAIMS_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "wallet_address": wallet_address,
                    "amount": str(amount),
                    "status": "pending",
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"faucet_claims insert failed for user {user_id}: {e}")
        return None


def mark_claim_sent(claim_id: int, tx_hash: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CLAIMS_TABLE).update(
            {"status": "sent", "tx_hash": tx_hash}
        ).eq("id", claim_id).execute()
    except Exception as e:
        logger.warning(f"faucet_claims mark-sent failed for claim {claim_id}: {e}")


def mark_claim_failed(claim_id: int, error: str) -> None:
    try:
        client = get_supabase_client()
        client.table(_CLAIMS_TABLE).update(
            {"status": "failed", "error": error}
        ).eq("id", claim_id).execute()
    except Exception as e:
        logger.warning(f"faucet_claims mark-failed failed for claim {claim_id}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/db/test_faucet.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/db/faucet.py tests/db/test_faucet.py
git commit -m "feat: add faucet DB access module"
```

---

### Task 5: `src/routes/faucet.py` — nonce + claim endpoints

**Files:**
- Create: `src/routes/faucet.py`
- Modify: `src/main.py`
- Test: `tests/routes/test_faucet.py`

**Interfaces:**
- Consumes: `has_completed_at_least_one_request`, `get_existing_claim`,
  `create_pending_claim`, `mark_claim_sent`, `mark_claim_failed` (Task 4);
  `WayzTokenFaucetClient`, `WayzTokenFaucetClientError` (Task 3);
  `Config.WAYZ_FAUCET_CLAIM_AMOUNT`, `Config.WAYZ_FAUCET_MIN_REQUESTS`
  (Task 2); `src.security.deps.get_user_id`;
  `src.services.endpoint_rate_limiter.create_endpoint_rate_limit`;
  `src.config.redis_config.get_redis_client`.
- Produces: FastAPI `router` with `POST /faucet/nonce`,
  `POST /faucet/claim`.

- [ ] **Step 1: Write the failing tests**

Create `tests/routes/test_faucet.py`:

```python
"""Tests for src.routes.faucet (gatewayz-backend#2245)."""

from unittest.mock import MagicMock, patch

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_user_id

client = TestClient(app)
app.dependency_overrides[get_user_id] = lambda: 42


def _signed_claim_body(user_id: int, nonce: str, account) -> dict:
    message = f"Claim testnet WAYZ for Gatewayz account {user_id}. Nonce: {nonce}."
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    return {"wallet_address": account.address, "signature": f"0x{signature}"}


@patch("src.routes.faucet.get_redis_client")
def test_nonce_endpoint_returns_a_nonce(mock_get_redis):
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    response = client.post("/faucet/nonce", json={"wallet_address": "0x" + "1" * 40})

    assert response.status_code == 200
    assert "message" in response.json()["data"]
    mock_redis.setex.assert_called_once()


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.create_pending_claim")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_succeeds_with_valid_signature(
    mock_get_redis, mock_eligible, mock_existing, mock_create, mock_client_cls
):
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.get.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = True
    mock_existing.return_value = None
    mock_create.return_value = {"id": 7, "user_id": 42, "wallet_address": account.address}

    mock_client_instance = MagicMock()

    async def _mint(*args, **kwargs):
        return "0xtxhash"

    mock_client_instance.mint = _mint
    mock_client_cls.from_config.return_value = mock_client_instance

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["tx_hash"] == "0xtxhash"
    mock_redis.delete.assert_called_once()


@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_missing_nonce(mock_get_redis):
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_get_redis.return_value = mock_redis

    account = Account.create()
    body = _signed_claim_body(42, "whatever-nonce", account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 400


@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_wrong_signer(mock_get_redis):
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.get.return_value = nonce
    mock_get_redis.return_value = mock_redis

    signer_account = Account.create()
    claimed_wallet = Account.create()  # different address than the actual signer
    body = _signed_claim_body(42, nonce, signer_account)
    body["wallet_address"] = claimed_wallet.address  # mismatch

    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 401


@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_ineligible_account(mock_get_redis, mock_eligible, mock_existing):
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.get.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = False

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 403


@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_rejects_duplicate(mock_get_redis, mock_eligible, mock_existing):
    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.get.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = True
    mock_existing.return_value = {"id": 1, "status": "sent"}

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 409


@patch("src.routes.faucet.WayzTokenFaucetClient")
@patch("src.routes.faucet.get_existing_claim")
@patch("src.routes.faucet.has_completed_at_least_one_request")
@patch("src.routes.faucet.get_redis_client")
def test_claim_returns_503_when_faucet_unconfigured(
    mock_get_redis, mock_eligible, mock_existing, mock_client_cls
):
    from src.services.chain.wayz_token_faucet_client import WayzTokenFaucetClientError

    account = Account.create()
    nonce = "test-nonce-123"
    mock_redis = MagicMock()
    mock_redis.get.return_value = nonce
    mock_get_redis.return_value = mock_redis
    mock_eligible.return_value = True
    mock_existing.return_value = None
    mock_client_cls.from_config.side_effect = WayzTokenFaucetClientError("not configured")

    body = _signed_claim_body(42, nonce, account)
    response = client.post("/faucet/claim", json=body)

    assert response.status_code == 503
```

Note: `account.sign_message(encode_defunct(text=message))` is the
real, unmocked `eth_account` signing call this test relies on for
`test_claim_succeeds_with_valid_signature` and
`test_claim_rejects_wrong_signer` — verify `.signature` is the right
attribute name on the result (matching this plan's Task 3 verification
approach) before assuming it; run a quick
`python3 -c "from eth_account import Account; from eth_account.messages import encode_defunct; a = Account.create(); print(dir(a.sign_message(encode_defunct(text='x'))))"`
if you want to double check.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/routes/test_faucet.py -v`
Expected: FAIL — `src.routes.faucet` module not found

- [ ] **Step 3: Write the implementation**

Create `src/routes/faucet.py`:

```python
"""WAYZ testnet faucet — claim testnet WAYZ via a signed wallet-ownership
proof (gatewayz-backend#2245). No wallet-to-account linkage exists yet
(Epic 2) -- wallet control is proven per-request instead of stored.
See docs/superpowers/specs/2026-09-01-wayz-testnet-faucet-design.md.
"""

import logging
import secrets
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.db.faucet import (
    create_pending_claim,
    get_existing_claim,
    has_completed_at_least_one_request,
    mark_claim_failed,
    mark_claim_sent,
)
from src.security.deps import get_user_id
from src.services.chain.wayz_token_faucet_client import (
    WayzTokenFaucetClient,
    WayzTokenFaucetClientError,
)
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_NONCE_TTL_SECONDS = 300
_NONCE_KEY_PREFIX = "faucet_nonce:"

faucet_nonce_rl = create_endpoint_rate_limit("faucet_nonce", max_requests=10, window_seconds=60)
faucet_claim_rl = create_endpoint_rate_limit("faucet_claim", max_requests=5, window_seconds=60)


class FaucetNonceRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42)


class FaucetClaimRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42)
    signature: str = Field(..., min_length=1)


def _nonce_key(user_id: int, wallet_address: str) -> str:
    return f"{_NONCE_KEY_PREFIX}{user_id}:{wallet_address.lower()}"


def _claim_message(user_id: int, nonce: str) -> str:
    return f"Claim testnet WAYZ for Gatewayz account {user_id}. Nonce: {nonce}."


@router.post("/faucet/nonce", tags=["faucet"])
async def get_faucet_nonce(
    body: FaucetNonceRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(faucet_nonce_rl),
) -> dict[str, Any]:
    """Issue a one-time nonce + the exact message the caller must sign."""
    nonce = secrets.token_hex(16)
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Faucet temporarily unavailable")

    redis_client.setex(_nonce_key(user_id, body.wallet_address), _NONCE_TTL_SECONDS, nonce)
    message = _claim_message(user_id, nonce)
    return {"success": True, "data": {"message": message, "expires_in": _NONCE_TTL_SECONDS}}


@router.post("/faucet/claim", tags=["faucet"])
async def claim_faucet(
    body: FaucetClaimRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(faucet_claim_rl),
) -> dict[str, Any]:
    """Verify wallet ownership, check eligibility, mint testnet WAYZ."""
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Faucet temporarily unavailable")

    key = _nonce_key(user_id, body.wallet_address)
    nonce = redis_client.get(key)
    if not nonce:
        raise HTTPException(
            status_code=400, detail="No pending nonce for this wallet — request one first"
        )
    nonce = nonce.decode() if isinstance(nonce, bytes) else nonce

    message = _claim_message(user_id, nonce)
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=body.signature)
    except Exception as e:
        redis_client.delete(key)
        raise HTTPException(status_code=401, detail="Invalid signature") from e

    redis_client.delete(key)

    if recovered.lower() != body.wallet_address.lower():
        raise HTTPException(status_code=401, detail="Signature does not match wallet_address")

    if not has_completed_at_least_one_request(user_id, Config.WAYZ_FAUCET_MIN_REQUESTS):
        raise HTTPException(status_code=403, detail="Account not eligible for the faucet yet")

    if get_existing_claim(user_id, body.wallet_address) is not None:
        raise HTTPException(status_code=409, detail="Already claimed")

    claim = create_pending_claim(user_id, body.wallet_address, Config.WAYZ_FAUCET_CLAIM_AMOUNT)
    if claim is None:
        raise HTTPException(status_code=409, detail="Already claimed")

    try:
        mint_client = WayzTokenFaucetClient.from_config()
    except WayzTokenFaucetClientError as e:
        mark_claim_failed(claim["id"], str(e))
        raise HTTPException(status_code=503, detail="Faucet not configured") from e

    try:
        tx_hash = await mint_client.mint(body.wallet_address, Config.WAYZ_FAUCET_CLAIM_AMOUNT)
    except Exception as e:
        logger.error(f"Faucet mint failed for claim {claim['id']}: {e}")
        mark_claim_failed(claim["id"], str(e))
        raise HTTPException(status_code=502, detail="Mint failed") from e

    mark_claim_sent(claim["id"], tx_hash)
    return {
        "success": True,
        "tx_hash": tx_hash,
        "amount": str(Config.WAYZ_FAUCET_CLAIM_AMOUNT),
    }
```

- [ ] **Step 4: Wire the router into `src/main.py`**

In `src/main.py`, find the list containing the `user_provider_keys` entry
(the one loaded via `load_route(module_name, display_name, app)` — the
non-v1, direct-on-`app` routes list). Add a new tuple immediately after
the `user_provider_keys` entry:

```python
        (
            "faucet",
            "WAYZ Testnet Faucet",
        ),  # Testnet WAYZ claim (gatewayz-backend#2245)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/routes/test_faucet.py -v`
Expected: PASS (7 tests)

Run: `python3 -c "from src.main import app; print('OK')"` — must import
cleanly (proves the router registration didn't break app startup).

- [ ] **Step 6: Run the full new-code test suite together**

Run: `pytest tests/routes/test_faucet.py tests/db/test_faucet.py tests/services/chain/test_wayz_token_faucet_client.py -v`
Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add src/routes/faucet.py src/main.py tests/routes/test_faucet.py
git commit -m "feat: add /faucet/nonce and /faucet/claim endpoints"
```

---

## Self-Review Notes

- **Spec coverage:** nonce+signed-message flow ✓ (Task 5), eligibility
  via usage_records ✓ (Task 4), one-claim-per-user/wallet via DB
  uniqueness ✓ (Task 1 + Task 4's `create_pending_claim` returning `None`
  on violation), separate signing-capable client ✓ (Task 3), config
  no-op-until-configured ✓ (Task 2 defaults + Task 5's 503 handling),
  concurrent-mint nonce safety via `asyncio.Lock` ✓ (Task 3). Non-goals
  (Genesis Points ledger, wallet linkage, on-chain faucet contract,
  reclaim flow) — no tasks needed, correctly out of scope.
- **Placeholder scan:** none found.
- **Type consistency:** `WayzTokenFaucetClient.mint(to_address, amount_wayz) -> str`
  used identically in Task 3's tests and Task 5's route handler.
  `create_pending_claim`/`get_existing_claim`/`mark_claim_sent`/
  `mark_claim_failed` signatures match between Task 4's implementation
  and Task 5's imports/calls. `FaucetClaimRequest`'s `wallet_address`/
  `signature` fields match what both test files construct.
- **API verification:** every web3.py/eth_account call in this plan
  (`build_transaction`, `sign_transaction().raw_transaction`,
  `send_raw_transaction`, `get_transaction_count`,
  `Account.recover_message`, `encode_defunct`) was checked against the
  actual installed library before being written into the plan, per the
  Global Constraints note about gatewayz-backend#2244's `get_logs` bug.
