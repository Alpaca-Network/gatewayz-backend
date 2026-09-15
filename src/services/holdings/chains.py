"""Read-only multi-chain ERC-20/native balance reader for holdings rewards.

Answers exactly one question: "how many raw token units does address X hold
right now, on each supported EVM chain?". Strictly **read-only** -- it never
signs, never sends a transaction, and never holds custody of anything. The
same posture as src/services/chain/wayz_staking_client.py, one chain wider.

Partial failure is a first-class outcome, not an exception. A wallet's reward
is derived from what it holds, so a chain that times out must never be
reported as a zero balance -- that would silently undervalue the wallet and
cost the user credits. Instead :func:`read_balances` returns a
:class:`BalanceReadResult` that carries both the readings it *did* get and the
chains it could not read, and the caller decides what to do with an incomplete
set.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from web3 import Web3

from src.config.config import Config

logger = logging.getLogger(__name__)

CHAIN_ID_ETHEREUM = 1
CHAIN_ID_BNB_CHAIN = 56
CHAIN_ID_POLYGON = 137
CHAIN_ID_BASE = 8453
CHAIN_ID_ARBITRUM_ONE = 42161
CHAIN_ID_AVALANCHE_C_CHAIN = 43114

CHAIN_NAMES: dict[int, str] = {
    CHAIN_ID_ETHEREUM: "ethereum",
    CHAIN_ID_BNB_CHAIN: "bnb-chain",
    CHAIN_ID_POLYGON: "polygon",
    CHAIN_ID_BASE: "base",
    CHAIN_ID_ARBITRUM_ONE: "arbitrum-one",
    CHAIN_ID_AVALANCHE_C_CHAIN: "avalanche-c-chain",
}

# Chain id -> the Config attribute holding that chain's RPC URL. Resolved at
# call time (not import time) so a test or a redeploy can change the env
# without re-importing this module.
_RPC_CONFIG_ATTR: dict[int, str] = {
    CHAIN_ID_ETHEREUM: "ETHEREUM_RPC_URL",
    CHAIN_ID_BNB_CHAIN: "BNB_CHAIN_RPC_URL",
    CHAIN_ID_POLYGON: "POLYGON_RPC_URL",
    CHAIN_ID_BASE: "BASE_RPC_URL",
    CHAIN_ID_ARBITRUM_ONE: "ARBITRUM_RPC_URL",
    CHAIN_ID_AVALANCHE_C_CHAIN: "AVALANCHE_RPC_URL",
}

SUPPORTED_CHAIN_IDS: tuple[int, ...] = tuple(sorted(_RPC_CONFIG_ATTR))

# Seconds to wait on a single RPC round trip. Public endpoints are slow and
# rate-limited; a chain that exceeds this is reported as failed, not as zero.
RPC_TIMEOUT_SECONDS = 10

# Minimal ERC-20 fragment -- balanceOf is the only call this module makes.
ERC20_BALANCE_OF_ABI: list[dict] = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]


class HoldingsChainError(Exception):
    """Base class for errors raised by the holdings balance reader."""


class InvalidWalletAddressError(HoldingsChainError):
    """The supplied wallet address is not a valid EVM address."""


class UnsupportedChainError(HoldingsChainError):
    """No RPC endpoint is configured for the requested chain id."""


@dataclass(frozen=True)
class TokenRef:
    """A token we are willing to value, on one specific chain.

    Attributes:
        chain_id: EVM chain id the token lives on.
        contract_address: ERC-20 contract address, or ``None`` for the chain's
            native coin (ETH, BNB, POL, AVAX, ...).
        decimals: Token decimals, used by the caller to scale ``raw_amount``.
            This module never scales -- integers only, no float drift.
        symbol: Display symbol, for logs and the user-facing breakdown.
        price_id: Identifier used by src.services.holdings.prices to fetch a
            USD price (a CoinGecko coin id).
    """

    chain_id: int
    contract_address: str | None
    decimals: int
    symbol: str
    price_id: str

    @property
    def is_native(self) -> bool:
        return self.contract_address is None


@dataclass(frozen=True)
class BalanceReading:
    """A successfully read balance, in the token's raw base units."""

    token: TokenRef
    raw_amount: int


@dataclass(frozen=True)
class ChainReadFailure:
    """A chain we could not read, and why.

    Its tokens are absent from ``BalanceReadResult.readings`` entirely -- an
    unread chain is never represented as a zero balance.
    """

    chain_id: int
    reason: str


@dataclass(frozen=True)
class BalanceReadResult:
    """Outcome of a multi-chain read.

    Invariant: every entry in ``readings`` comes from a chain that was read
    **completely**. If any token on a chain failed, that whole chain is listed
    in ``failures`` and contributes no readings, so the caller can never
    mistake a partial set for a complete one.

    Callers valuing a wallet must check :attr:`is_complete` (or
    :attr:`failed_chain_ids`) before treating the total as authoritative.
    """

    readings: list[BalanceReading] = field(default_factory=list)
    failures: list[ChainReadFailure] = field(default_factory=list)

    @property
    def failed_chain_ids(self) -> list[int]:
        return [failure.chain_id for failure in self.failures]

    @property
    def is_complete(self) -> bool:
        """True when every requested chain was read successfully."""
        return not self.failures


def rpc_url_for_chain(chain_id: int) -> str:
    """Return the configured RPC URL for ``chain_id``.

    Raises:
        UnsupportedChainError: if the chain is not one of the supported EVM
            chains, or its RPC URL has been explicitly configured empty.
    """
    attr = _RPC_CONFIG_ATTR.get(chain_id)
    if attr is None:
        raise UnsupportedChainError(f"Unsupported chain id: {chain_id}")

    url = getattr(Config, attr, None)
    if not url:
        raise UnsupportedChainError(f"{attr} is not configured (chain id {chain_id})")

    return url


def _make_web3(chain_id: int, rpc_url: str) -> Web3:
    """Build a read-only web3 client for one chain.

    Separated out so tests can substitute a client without patching the whole
    Web3 class, and so every chain gets its own connection with an explicit
    timeout.
    """
    logger.debug("Building holdings RPC client for chain %s", CHAIN_NAMES.get(chain_id, chain_id))
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_SECONDS}))


def _read_one_token(client: Web3, wallet_address: str, token: TokenRef) -> int:
    if token.is_native:
        return int(client.eth.get_balance(wallet_address))

    contract = client.eth.contract(
        address=Web3.to_checksum_address(token.contract_address),
        abi=ERC20_BALANCE_OF_ABI,
    )
    return int(contract.functions.balanceOf(wallet_address).call())


def read_balances(wallet_address: str, tokens: list[TokenRef]) -> BalanceReadResult:
    """Read ``wallet_address``'s balance of every token in ``tokens``.

    Tokens are grouped by chain and each chain gets exactly one RPC client,
    so N tokens on one chain cost one connection, not N. Native coins are read
    with ``eth_getBalance``; ERC-20s with a ``balanceOf`` call.

    A chain that errors, times out, or has no configured endpoint is recorded
    in the result's ``failures`` and contributes **no** readings -- the other
    chains are still read and returned. Nothing here raises on an RPC problem;
    the only exception raised is for an invalid wallet address, which is a
    programming/input error rather than a transient one.

    Args:
        wallet_address: EVM address, in any case. Validated and converted to
            EIP-55 checksum form before any network call is made.
        tokens: Tokens to read. May span any mix of supported chains.

    Returns:
        A :class:`BalanceReadResult`. Amounts are raw integers in the token's
        base units -- scaling by ``TokenRef.decimals`` is the caller's job.

    Raises:
        InvalidWalletAddressError: if ``wallet_address`` is not a valid EVM
            address. Raised before any client is constructed.
    """
    checksum_address = _checksum(wallet_address)

    result_readings: list[BalanceReading] = []
    failures: list[ChainReadFailure] = []

    for chain_id, chain_tokens in _group_by_chain(tokens).items():
        try:
            rpc_url = rpc_url_for_chain(chain_id)
            client = _make_web3(chain_id, rpc_url)
            # Collected per chain and only merged on full success, so a
            # mid-chain failure can't leak a partial set into the result.
            chain_readings = [
                BalanceReading(
                    token=token, raw_amount=_read_one_token(client, checksum_address, token)
                )
                for token in chain_tokens
            ]
        except Exception as exc:  # noqa: BLE001 - one bad chain must not sink the rest
            logger.warning(
                "Holdings balance read failed for chain %s: %s",
                CHAIN_NAMES.get(chain_id, chain_id),
                exc,
            )
            failures.append(ChainReadFailure(chain_id=chain_id, reason=str(exc)))
            continue

        result_readings.extend(chain_readings)

    return BalanceReadResult(readings=result_readings, failures=failures)


def _checksum(wallet_address: str) -> str:
    try:
        return Web3.to_checksum_address(wallet_address)
    except Exception as exc:
        raise InvalidWalletAddressError(f"Invalid EVM wallet address: {wallet_address!r}") from exc


def _group_by_chain(tokens: list[TokenRef]) -> dict[int, list[TokenRef]]:
    grouped: dict[int, list[TokenRef]] = defaultdict(list)
    for token in tokens:
        grouped[token.chain_id].append(token)
    return dict(grouped)
