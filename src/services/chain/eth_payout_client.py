"""Signing-capable web3.py client for settling community-GPU provider
payouts in native ETH on Base (product decision 2026-09-22: WAYZ is not
going public for now, so providers are paid in ETH instead).

Replaced the (now removed) WAYZ ERC-20 rewards client on the settlement path.
Holds the payout pool EOA's private key (Config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY)
and sends plain value transfers (21000 gas, EIP-1559 fees) out of it. Also
reads the Chainlink ETH/USD aggregator on Base, which settlement uses to
convert USD-denominated earnings to wei at payout time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from eth_account import Account
from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

_NATIVE_TRANSFER_GAS = 21_000

# Minimal Chainlink AggregatorV3Interface ABI -- only what we read.
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
    """The ETH/USD feed answer is missing, non-positive, or too old to pay at."""


@dataclass(frozen=True)
class EthUsdPrice:
    """Chainlink answer as an integer scaled by `decimals` (e.g. 8)."""

    answer: int
    decimals: int
    updated_at: int  # unix seconds

    @property
    def usd_per_eth(self) -> Decimal:
        return Decimal(self.answer) / (Decimal(10) ** self.decimals)


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


class EthPayoutClient:
    """Signs and sends native ETH transfers out of the payout pool EOA on Base."""

    def __init__(self, rpc_url: str, private_key: str, feed_address: str, chain_id: int):
        self._to_checksum_address = Web3.to_checksum_address
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)
        self._chain_id = chain_id
        self._feed = self._w3.eth.contract(
            address=self._to_checksum_address(feed_address), abi=_AGGREGATOR_ABI
        )

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
        )

    @property
    def pool_address(self) -> str:
        return self._account.address

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

    async def transfer(self, to_address: str, amount_wei: int) -> str:
        """Send amount_wei of native ETH to to_address. Returns the tx hash.

        Serialized by a module-level asyncio.Lock -- the pool account's
        nonce would race under concurrent settlement runs otherwise.
        """
        async with _transfer_lock:
            return await asyncio.to_thread(self._transfer_sync, to_address, amount_wei)

    def _transfer_sync(self, to_address: str, amount_wei: int) -> str:
        checksum_to = self._to_checksum_address(to_address)
        nonce = self._w3.eth.get_transaction_count(self._account.address, "pending")
        latest = self._w3.eth.get_block("latest")
        base_fee = int(latest.get("baseFeePerGas", 0) or 0)
        priority_fee = int(self._w3.eth.max_priority_fee)
        tx = {
            "from": self._account.address,
            "to": checksum_to,
            "value": amount_wei,
            "nonce": nonce,
            "chainId": self._chain_id,
            "gas": _NATIVE_TRANSFER_GAS,
            "maxPriorityFeePerGas": priority_fee,
            # 2x base fee headroom -- standard EIP-1559 practice so the tx
            # survives a few blocks of base-fee increase.
            "maxFeePerGas": 2 * base_fee + priority_fee,
            "type": 2,
        }
        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.to_0x_hex()

    def pool_balance_wei(self) -> int:
        """Current native ETH balance (wei) of the pool EOA. Read-only, sync."""
        return int(self._w3.eth.get_balance(self._account.address))

    def get_receipt(self, tx_hash: str) -> dict | None:
        """Transaction receipt, or None if not mined / dropped (web3.py
        raises TransactionNotFound for that; normalized here)."""
        from web3.exceptions import TransactionNotFound

        try:
            receipt = self._w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            return None
        return dict(receipt)
