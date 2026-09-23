"""Signing-capable web3.py client for settling community-GPU provider
payouts in native ETH on Base (product decision 2026-09-22: WAYZ is not
going public for now, so providers are paid in ETH instead).

Replaced the (now removed) WAYZ ERC-20 rewards client on the settlement path.
Holds the payout pool EOA's private key (Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY)
and sends native value transfers (EIP-1559) out of it. Also reads the
Chainlink ETH/USD aggregator and the L2 sequencer-uptime feed on Base, which
settlement uses to convert USD-denominated earnings to wei at payout time.

**Double-pay safety (PR #2364 review):** `transfer()` signs locally, derives
the tx hash from the signed bytes, and hands it (with the nonce) to the
caller's `record` callback BEFORE broadcasting. Only a failure before that
point raises `TransferNotSentError` (provably never broadcast -- safe to
revert the earnings). Any failure at or after broadcast raises
`TransferBroadcastError`: the node may have accepted the tx, so the caller
must leave the settlement pending for receipt/nonce reconciliation and
never revert the earnings while that recorded hash could still land.

**Gas (PR #2364 review):** the gas limit is `eth_estimateGas` plus
headroom, never a hard-coded 21000 -- a payout wallet can be a smart
contract (e.g. Coinbase Smart Wallet on Base) whose `receive()` costs more
than a plain EOA transfer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from eth_account import Account
from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

_MIN_TRANSFER_GAS = 21_000
_RPC_TIMEOUT_SECONDS = 10
# Estimate x 1.25 -- smart-wallet receive() gas can vary slightly between
# estimation and inclusion; unused gas is refunded, so headroom is cheap.
_GAS_HEADROOM_NUM = 5
_GAS_HEADROOM_DEN = 4

# Minimal Chainlink AggregatorV3Interface ABI -- only what we read. The
# sequencer-uptime feed exposes the same latestRoundData() shape.
_AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

_transfer_lock = asyncio.Lock()


class EthPayoutClientError(Exception):
    """Raised when the client can't be constructed (e.g. pool key not configured)."""


class StalePriceError(Exception):
    """The ETH/USD price can't be trusted right now: feed answer missing,
    non-positive, or too old, or the Base sequencer is down / inside its
    post-restart grace period."""


class TransferNotSentError(Exception):
    """The transfer provably never reached the network (failed while
    estimating gas, signing, or recording the hash). Safe to revert."""


class TransferBroadcastError(Exception):
    """Broadcast was attempted and its outcome is unknown -- the node may
    have accepted the tx. NOT safe to revert; reconcile by receipt/nonce."""

    def __init__(self, tx_hash: str, nonce: int, cause: Exception):
        super().__init__(f"broadcast of {tx_hash} (nonce {nonce}) failed: {cause}")
        self.tx_hash = tx_hash
        self.nonce = nonce
        self.cause = cause


@dataclass(frozen=True)
class EthUsdPrice:
    """Chainlink answer as an integer scaled by `decimals` (e.g. 8)."""

    answer: int
    decimals: int
    updated_at: int  # unix seconds

    @property
    def usd_per_eth(self) -> Decimal:
        return Decimal(self.answer) / (Decimal(10) ** self.decimals)


@dataclass(frozen=True)
class SequencerStatus:
    """Chainlink L2 sequencer-uptime feed: answer 0 == up, 1 == down;
    started_at == when the current status began."""

    answer: int
    started_at: int


@dataclass(frozen=True)
class SignedTransfer:
    tx_hash: str
    nonce: int
    gas: int
    to: str
    amount_wei: int


def validate_price(price: EthUsdPrice, max_age_seconds: int, now: float | None = None) -> None:
    """Raise StalePriceError unless the answer is positive and fresh.
    Pure -- the settlement run calls this before paying anyone."""
    if price.answer <= 0:
        raise StalePriceError(f"ETH/USD feed returned non-positive answer {price.answer}")
    age = (time.time() if now is None else now) - price.updated_at
    if age > max_age_seconds:
        raise StalePriceError(
            f"ETH/USD feed answer is {int(age)}s old (max {max_age_seconds}s) -- refusing to pay"
        )


def validate_sequencer(
    status: SequencerStatus, grace_period_seconds: int, now: float | None = None
) -> None:
    """Chainlink's recommended L2 check: refuse to use price data while the
    sequencer is down, or within `grace_period_seconds` of it coming back
    up (feeds may still be catching up). Pure."""
    if status.answer != 0:
        raise StalePriceError("Base sequencer is down -- refusing to pay")
    since_up = (time.time() if now is None else now) - status.started_at
    if since_up < grace_period_seconds:
        raise StalePriceError(
            f"Base sequencer came back up {int(since_up)}s ago (grace "
            f"{grace_period_seconds}s) -- refusing to pay"
        )


def usd_micros_to_wei(usd_micros: int, price: EthUsdPrice) -> int:
    """Convert USD micro-dollars to ETH wei at `price`, floor-rounded
    (never overpay). All-integer math:

        wei = usd_micros * 1e18 * 10**decimals / (1e6 * answer)
    """
    if usd_micros <= 0:
        return 0
    numerator = usd_micros * 10**18 * 10**price.decimals
    denominator = 10**6 * price.answer
    return numerator // denominator


def wei_to_usd_micros(wei: int, price: EthUsdPrice) -> int:
    """Inverse of usd_micros_to_wei (floor) -- used for pool-balance logging."""
    if wei <= 0:
        return 0
    return (wei * 10**6 * price.answer) // (10**18 * 10**price.decimals)


def gas_limit_with_headroom(estimate: int) -> int:
    return max(_MIN_TRANSFER_GAS, -(-estimate * _GAS_HEADROOM_NUM // _GAS_HEADROOM_DEN))


class EthPriceReader:
    """Read-only Base feed reader (no key) -- ETH/USD + sequencer uptime.
    Used by settlement (via EthPayoutClient) and by the earnings endpoints
    to show ETH-equivalents of USD balances."""

    def __init__(self, rpc_url: str, feed_address: str, sequencer_feed_address: str | None):
        self._to_checksum_address = Web3.to_checksum_address
        self._w3 = Web3(
            Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": _RPC_TIMEOUT_SECONDS})
        )
        self._feed = self._w3.eth.contract(
            address=self._to_checksum_address(feed_address), abi=_AGGREGATOR_ABI
        )
        self._sequencer_feed = (
            self._w3.eth.contract(
                address=self._to_checksum_address(sequencer_feed_address), abi=_AGGREGATOR_ABI
            )
            if sequencer_feed_address
            else None
        )

    @classmethod
    def from_config(cls) -> EthPriceReader:
        if not Config.BASE_ETH_USD_FEED_ADDRESS:
            raise EthPayoutClientError("BASE_ETH_USD_FEED_ADDRESS is not set")
        return cls(
            Config.BASE_RPC_URL,
            Config.BASE_ETH_USD_FEED_ADDRESS,
            Config.BASE_SEQUENCER_UPTIME_FEED_ADDRESS,
        )

    def eth_usd_price(self) -> EthUsdPrice:
        """Latest Chainlink ETH/USD answer. Read-only, synchronous."""
        decimals = int(self._feed.functions.decimals().call())
        (
            _round_id,
            answer,
            _started,
            updated_at,
            _answered,
        ) = self._feed.functions.latestRoundData().call()
        return EthUsdPrice(answer=int(answer), decimals=decimals, updated_at=int(updated_at))

    def sequencer_status(self) -> SequencerStatus | None:
        """Base L2 sequencer-uptime status, or None when no feed is configured."""
        if self._sequencer_feed is None:
            return None
        (
            _round_id,
            answer,
            started_at,
            _updated,
            _answered,
        ) = self._sequencer_feed.functions.latestRoundData().call()
        return SequencerStatus(answer=int(answer), started_at=int(started_at))

    def trusted_eth_usd_price(self, now: float | None = None) -> EthUsdPrice:
        """Sequencer check (if configured) + fresh positive ETH/USD answer,
        or StalePriceError. The one gate every payout conversion goes through."""
        status = self.sequencer_status()
        if status is not None:
            validate_sequencer(status, Config.BASE_SEQUENCER_GRACE_PERIOD_SECONDS, now)
        price = self.eth_usd_price()
        validate_price(price, Config.ETH_USD_PRICE_MAX_AGE_SECONDS, now)
        return price


class EthPayoutClient(EthPriceReader):
    """Signs and sends native ETH transfers out of the payout pool EOA on Base."""

    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        feed_address: str,
        chain_id: int,
        sequencer_feed_address: str | None = None,
    ):
        super().__init__(rpc_url, feed_address, sequencer_feed_address)
        self._account = Account.from_key(private_key)
        self._chain_id = chain_id

    @classmethod
    def from_config(cls) -> EthPayoutClient:
        if Config.PROVIDER_PAYOUT_ASSET != "ETH":
            raise EthPayoutClientError(
                f"PROVIDER_PAYOUT_ASSET={Config.PROVIDER_PAYOUT_ASSET!r} is not supported "
                "(only 'ETH')"
            )
        if not Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY:
            raise EthPayoutClientError("PROVIDER_PAYOUT_POOL_PRIVATE_KEY is not set")
        if not Config.BASE_ETH_USD_FEED_ADDRESS:
            raise EthPayoutClientError("BASE_ETH_USD_FEED_ADDRESS is not set")
        return cls(
            Config.BASE_RPC_URL,
            Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY,
            Config.BASE_ETH_USD_FEED_ADDRESS,
            Config.BASE_CHAIN_ID,
            Config.BASE_SEQUENCER_UPTIME_FEED_ADDRESS,
        )

    @property
    def pool_address(self) -> str:
        return self._account.address

    async def transfer(
        self,
        to_address: str,
        amount_wei: int,
        record: Callable[[SignedTransfer], bool],
    ) -> SignedTransfer:
        """Sign, record, then broadcast a native ETH transfer.

        `record(signed)` runs BEFORE broadcast and must durably persist
        `signed.tx_hash`/`signed.nonce`; returning False aborts without
        sending. Raises TransferNotSentError (never broadcast) or
        TransferBroadcastError (outcome unknown -- see module docstring).

        Serialized by a module-level asyncio.Lock -- the pool account's
        nonce would race under concurrent settlement runs otherwise.
        """
        async with _transfer_lock:
            return await asyncio.to_thread(self._transfer_sync, to_address, amount_wei, record)

    def _sign(self, to_address: str, amount_wei: int):
        checksum_to = self._to_checksum_address(to_address)
        nonce = self._w3.eth.get_transaction_count(self._account.address, "pending")
        estimate = int(
            self._w3.eth.estimate_gas(
                {"from": self._account.address, "to": checksum_to, "value": amount_wei}
            )
        )
        gas = gas_limit_with_headroom(estimate)
        latest = self._w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas", 0) or 0)
        priority_fee = int(self._w3.eth.max_priority_fee)
        tx = {
            "from": self._account.address,
            "to": checksum_to,
            "value": amount_wei,
            "nonce": nonce,
            "chainId": self._chain_id,
            "gas": gas,
            "maxPriorityFeePerGas": priority_fee,
            # 2x base fee headroom -- standard EIP-1559 practice so the tx
            # survives a few blocks of base-fee increase.
            "maxFeePerGas": 2 * base_fee + priority_fee,
            "type": 2,
        }
        signed = self._account.sign_transaction(tx)
        return signed, SignedTransfer(
            tx_hash=signed.hash.to_0x_hex(),
            nonce=nonce,
            gas=gas,
            to=checksum_to,
            amount_wei=amount_wei,
        )

    def _transfer_sync(
        self, to_address: str, amount_wei: int, record: Callable[[SignedTransfer], bool]
    ) -> SignedTransfer:
        try:
            signed, info = self._sign(to_address, amount_wei)
        except Exception as e:
            raise TransferNotSentError(f"could not prepare transfer: {e}") from e
        try:
            recorded = record(info)
        except Exception as e:
            raise TransferNotSentError(f"could not record tx hash before send: {e}") from e
        if not recorded:
            raise TransferNotSentError("could not record tx hash before send")
        try:
            self._w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception as e:
            raise TransferBroadcastError(info.tx_hash, info.nonce, e) from e
        return info

    def pool_balance_wei(self) -> int:
        """Current native ETH balance (wei) of the pool EOA. Read-only, sync."""
        return int(self._w3.eth.get_balance(self._account.address))

    def confirmed_nonce(self) -> int:
        """Number of txs from the pool EOA already MINED (the 'latest' nonce).
        A recorded tx whose nonce is below this and has no receipt was
        replaced by another tx and can never land."""
        return int(self._w3.eth.get_transaction_count(self._account.address, "latest"))

    def get_receipt(self, tx_hash: str) -> dict | None:
        """Transaction receipt, or None if not mined / dropped (web3.py
        raises TransactionNotFound for that; normalized here)."""
        from web3.exceptions import TransactionNotFound

        try:
            receipt = self._w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        return dict(receipt)

    def wait_for_receipt(self, tx_hash: str, timeout_seconds: float) -> dict | None:
        """Poll for a receipt up to timeout_seconds; None on timeout."""
        from web3.exceptions import TimeExhausted

        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=timeout_seconds, poll_latency=2
            )
        except TimeExhausted:
            return None
        return dict(receipt)
