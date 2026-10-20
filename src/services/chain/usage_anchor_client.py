"""Signing web3.py client for the UsageAnchor contract on Avalanche C-Chain
(docs/usage-anchor/README.md).

Writes one Merkle root per UTC day with `anchor(day, root)` and reads it
back with `rootOf(day)`. Only the root goes on-chain -- the day's leaf
(request) count is deliberately not published. The contract only accepts calls from its
immutable `anchorer`, refuses a second root for the same day, and refuses an
empty root -- so a bug here can at worst fail to anchor, never rewrite an
anchored day.

Same double-send safety as eth_payout_client.py: `send_anchor()` signs
locally, derives the tx hash, and hands it (with the nonce) to the caller's
`record` callback BEFORE broadcasting. A failure before that point raises
`AnchorNotSentError` (provably never broadcast); a failure at or after
broadcast raises `AnchorBroadcastError` (the node may have accepted it, so
the caller keeps the row pending and the confirmation sweep reconciles).

Synchronous by design: callers run it in a worker thread
(asyncio.to_thread), never on the event loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from eth_account import Account
from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

_RPC_TIMEOUT_SECONDS = 10
# Estimate x 1.25 -- unused gas is refunded, so headroom is cheap.
_GAS_HEADROOM_NUM = 5
_GAS_HEADROOM_DEN = 4
_ZERO_ROOT = b"\x00" * 32

AVALANCHE_MAINNET_CHAIN_ID = 43114
AVALANCHE_FUJI_CHAIN_ID = 43113

# Minimal UsageAnchor ABI -- only what the backend calls. Must match
# Alpaca-Network/gatewayz-protocol src/UsageAnchor.sol.
USAGE_ANCHOR_ABI = [
    {
        "inputs": [
            {"internalType": "uint64", "name": "day", "type": "uint64"},
            {"internalType": "bytes32", "name": "root", "type": "bytes32"},
        ],
        "name": "anchor",
        "outputs": [{"internalType": "uint64", "name": "seq", "type": "uint64"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint64", "name": "day", "type": "uint64"}],
        "name": "rootOf",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "anchorer",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestDay",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "anchorCount",
        "outputs": [{"internalType": "uint64", "name": "", "type": "uint64"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint64", "name": "seq", "type": "uint64"},
            {"indexed": True, "internalType": "uint64", "name": "day", "type": "uint64"},
            {"indexed": False, "internalType": "bytes32", "name": "root", "type": "bytes32"},
        ],
        "name": "Anchored",
        "type": "event",
    },
]

# One anchoring signer per process: its nonce would race otherwise. The
# daily job and the admin trigger both go through here (in worker threads).
_send_lock = threading.Lock()


class UsageAnchorClientError(Exception):
    """Raised when the client can't be constructed (contract/key not configured)."""


class AnchorNotSentError(Exception):
    """The anchor tx provably never reached the network. Safe to retry."""


class AnchorBroadcastError(Exception):
    """Broadcast was attempted and its outcome is unknown. NOT safe to
    resend blindly; reconcile by receipt / on-chain rootOf."""

    def __init__(self, tx_hash: str, nonce: int, cause: Exception):
        super().__init__(f"broadcast of {tx_hash} (nonce {nonce}) failed: {cause}")
        self.tx_hash = tx_hash
        self.nonce = nonce
        self.cause = cause


@dataclass(frozen=True)
class SignedAnchor:
    tx_hash: str
    nonce: int
    gas: int
    day: int
    root: bytes


def explorer_base(chain_id: int) -> str | None:
    """Snowtrace base URL for the chain, or None for an unknown chain."""
    if chain_id == AVALANCHE_MAINNET_CHAIN_ID:
        return "https://snowtrace.io"
    if chain_id == AVALANCHE_FUJI_CHAIN_ID:
        return "https://testnet.snowtrace.io"
    return None


def gas_limit_with_headroom(estimate: int) -> int:
    return -(-estimate * _GAS_HEADROOM_NUM // _GAS_HEADROOM_DEN)


def anchorer_address_from_config() -> str | None:
    """Address of the configured signer key, without touching the network.
    Never returns or logs the key itself."""
    if not Config.USAGE_ANCHOR_PRIVATE_KEY:
        return None
    try:
        return Account.from_key(Config.USAGE_ANCHOR_PRIVATE_KEY).address
    except Exception:
        return None


class UsageAnchorReader:
    """Read-only access to UsageAnchor (no key)."""

    def __init__(self, rpc_url: str, contract_address: str, chain_id: int):
        self._w3 = Web3(
            Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": _RPC_TIMEOUT_SECONDS})
        )
        self._chain_id = chain_id
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=USAGE_ANCHOR_ABI
        )

    @property
    def chain_id(self) -> int:
        return self._chain_id

    @property
    def contract_address(self) -> str:
        return self._contract.address

    def root_of(self, day: int) -> bytes | None:
        """The anchored root for `day`, or None if the day isn't anchored."""
        root = bytes(self._contract.functions.rootOf(day).call())
        return None if root == _ZERO_ROOT else root

    def latest_day(self) -> int:
        return int(self._contract.functions.latestDay().call())

    def anchor_count(self) -> int:
        return int(self._contract.functions.anchorCount().call())

    def anchorer(self) -> str:
        return Web3.to_checksum_address(self._contract.functions.anchorer().call())

    def chain_id_onchain(self) -> int:
        return int(self._w3.eth.chain_id)

    def block_number(self) -> int:
        return int(self._w3.eth.block_number)

    def get_receipt(self, tx_hash: str) -> dict | None:
        """Transaction receipt, or None if not mined / dropped."""
        from web3.exceptions import TransactionNotFound

        try:
            receipt = self._w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        return dict(receipt)

    def wait_for_receipt(self, tx_hash: str, timeout_seconds: float) -> dict | None:
        from web3.exceptions import TimeExhausted

        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=timeout_seconds, poll_latency=2
            )
        except TimeExhausted:
            return None
        return dict(receipt)


class UsageAnchorClient(UsageAnchorReader):
    """Signs and sends `anchor()` from the dedicated anchoring key."""

    def __init__(self, rpc_url: str, contract_address: str, chain_id: int, private_key: str):
        super().__init__(rpc_url, contract_address, chain_id)
        self._account = Account.from_key(private_key)

    @classmethod
    def from_config(cls) -> UsageAnchorClient:
        if not Config.USAGE_ANCHOR_CONTRACT_ADDRESS:
            raise UsageAnchorClientError("USAGE_ANCHOR_CONTRACT_ADDRESS is not set")
        if not Config.USAGE_ANCHOR_PRIVATE_KEY:
            raise UsageAnchorClientError("USAGE_ANCHOR_PRIVATE_KEY is not set")
        if (
            Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY
            and Config.USAGE_ANCHOR_PRIVATE_KEY == Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY
        ):
            raise UsageAnchorClientError(
                "USAGE_ANCHOR_PRIVATE_KEY must be a dedicated key, not the payout-pool key"
            )
        try:
            return cls(
                Config.USAGE_ANCHOR_RPC_URL,
                Config.USAGE_ANCHOR_CONTRACT_ADDRESS,
                Config.USAGE_ANCHOR_CHAIN_ID,
                Config.USAGE_ANCHOR_PRIVATE_KEY,
            )
        except ValueError as e:
            # Never echo the key -- only say which setting is malformed.
            raise UsageAnchorClientError(
                f"USAGE_ANCHOR_CONTRACT_ADDRESS or USAGE_ANCHOR_PRIVATE_KEY is malformed "
                f"({type(e).__name__})"
            ) from None

    @property
    def signer_address(self) -> str:
        return self._account.address

    def balance_wei(self) -> int:
        return int(self._w3.eth.get_balance(self._account.address))

    def confirmed_nonce(self) -> int:
        """Txs from the signer already mined. A recorded tx whose nonce is
        below this and has no receipt was replaced and can never land."""
        return int(self._w3.eth.get_transaction_count(self._account.address, "latest"))

    def check_ready(self) -> None:
        """Refuse to send unless the RPC is the configured chain and this key
        is the contract's anchorer. Raises AnchorNotSentError."""
        onchain = self.chain_id_onchain()
        if onchain != self._chain_id:
            raise AnchorNotSentError(
                f"RPC reports chain id {onchain}, USAGE_ANCHOR_CHAIN_ID is {self._chain_id}"
            )
        anchorer = self.anchorer()
        if anchorer != self._account.address:
            raise AnchorNotSentError(
                f"signer {self._account.address} is not the contract's anchorer {anchorer}"
            )

    def _sign(self, day: int, root: bytes):
        sender = self._account.address
        nonce = self._w3.eth.get_transaction_count(sender, "pending")
        call = self._contract.functions.anchor(day, root)
        estimate = int(call.estimate_gas({"from": sender}))
        gas = gas_limit_with_headroom(estimate)
        latest = self._w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas", 0) or 0)
        priority_fee = int(self._w3.eth.max_priority_fee)
        tx = call.build_transaction(
            {
                "from": sender,
                "nonce": nonce,
                "chainId": self._chain_id,
                "gas": gas,
                "maxPriorityFeePerGas": priority_fee,
                # 2x base fee headroom so the tx survives a few blocks of
                # base-fee increase.
                "maxFeePerGas": 2 * base_fee + priority_fee,
                "type": 2,
            }
        )
        signed = self._account.sign_transaction(tx)
        return signed, SignedAnchor(
            tx_hash=signed.hash.to_0x_hex(),
            nonce=nonce,
            gas=gas,
            day=day,
            root=root,
        )

    def send_anchor(
        self,
        day: int,
        root: bytes,
        record: Callable[[SignedAnchor], bool],
    ) -> SignedAnchor:
        """Check readiness, sign, record, then broadcast `anchor(day, root)`.
        `record(signed)` must durably persist the tx hash and
        nonce; returning False aborts without sending."""
        if len(root) != 32 or root == _ZERO_ROOT:
            raise AnchorNotSentError("root must be 32 non-zero bytes")
        with _send_lock:
            try:
                self.check_ready()
                signed, info = self._sign(day, root)
            except AnchorNotSentError:
                raise
            except Exception as e:
                raise AnchorNotSentError(f"could not prepare anchor tx: {e}") from e
            try:
                recorded = record(info)
            except Exception as e:
                raise AnchorNotSentError(f"could not record tx hash before send: {e}") from e
            if not recorded:
                raise AnchorNotSentError("could not record tx hash before send")
            try:
                self._w3.eth.send_raw_transaction(signed.raw_transaction)
            except Exception as e:
                raise AnchorBroadcastError(info.tx_hash, info.nonce, e) from e
            logger.info(
                "Usage anchor broadcast: day=%s tx=%s nonce=%s",
                day,
                info.tx_hash,
                info.nonce,
            )
            return info
