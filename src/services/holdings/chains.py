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
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

import requests
from web3 import Web3
from web3.exceptions import (
    BadResponseFormat,
    ContractLogicError,
    ProviderConnectionError,
    TimeExhausted,
    TooManyRequests,
    Web3RPCError,
)

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

# Chain id -> (public default, secondary public endpoint). The default must
# match that chain's Config default (tests/services/holdings/test_chains.py
# guards the drift); the secondary is a different operator, so one public
# provider changing its terms -- polygon-rpc.com starting to answer 401 in
# Oct 2026 -- does not take the chain down. Each secondary was checked on
# 2026-10-08 to answer eth_chainId with the right id.
_PUBLIC_RPC_URLS: dict[int, tuple[str, str]] = {
    CHAIN_ID_ETHEREUM: ("https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"),
    CHAIN_ID_BNB_CHAIN: ("https://bsc-dataseed.binance.org", "https://bsc.publicnode.com"),
    CHAIN_ID_POLYGON: ("https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic"),
    CHAIN_ID_BASE: ("https://mainnet.base.org", "https://base-rpc.publicnode.com"),
    CHAIN_ID_ARBITRUM_ONE: (
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one-rpc.publicnode.com",
    ),
    CHAIN_ID_AVALANCHE_C_CHAIN: (
        "https://api.avax.network/ext/bc/C/rpc",
        "https://avalanche-c-chain-rpc.publicnode.com",
    ),
}

# Chain id -> Alchemy network slug, for https://{slug}.g.alchemy.com/v2/{key}.
# Slugs from Alchemy's endpoint reference (alchemy.com/docs).
_ALCHEMY_NETWORKS: dict[int, str] = {
    CHAIN_ID_ETHEREUM: "eth-mainnet",
    CHAIN_ID_BNB_CHAIN: "bnb-mainnet",
    CHAIN_ID_POLYGON: "polygon-mainnet",
    CHAIN_ID_BASE: "base-mainnet",
    CHAIN_ID_ARBITRUM_ONE: "arb-mainnet",
    CHAIN_ID_AVALANCHE_C_CHAIN: "avax-mainnet",
}

# JSON-RPC error codes that mean "this endpoint is refusing us" rather than
# anything about the call itself: -32005 (limit exceeded), -32016 (Alchemy
# over rate limit), 429 (some providers echo the HTTP status as the code).
_RATE_LIMIT_RPC_CODES = frozenset({-32005, -32016, 429})

# How long an endpoint that just failed on transport is tried *last* rather
# than first. It is never skipped -- only reordered -- so a chain whose
# every endpoint had a blip is still attempted on all of them.
_ENDPOINT_DEMOTION_SECONDS = 300
_demoted_until: dict[str, float] = {}

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
class RpcEndpoint:
    """One RPC URL a chain can be read from, and where it came from.

    ``source`` is ``"env"`` (an explicit <CHAIN>_RPC_URL), ``"alchemy"``
    (derived from ALCHEMY_API_KEY) or ``"public"``. The URL can embed an API
    key, so it is excluded from ``repr`` and logs name the source instead.
    """

    url: str = field(repr=False)
    source: str


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


def rpc_endpoints_for_chain(chain_id: int) -> list[RpcEndpoint]:
    """Every endpoint ``chain_id`` may be read from, in the order to try them.

    The primary is the first of: an explicit ``<CHAIN>_RPC_URL`` that differs
    from the public default, the Alchemy URL for ``ALCHEMY_API_KEY``, or the
    public default. A configured value *equal* to the public default is
    indistinguishable from leaving it unset and is treated as such, so
    setting ALCHEMY_API_KEY upgrades it. After the primary come any remaining
    endpoints from that list plus the secondary public endpoint, which is what
    :func:`read_balances` falls back to on a transport failure. Endpoints that
    failed on transport in the last few minutes move to the back.

    Raises:
        UnsupportedChainError: if the chain is not one of the supported EVM
            chains.
    """
    attr = _RPC_CONFIG_ATTR.get(chain_id)
    if attr is None:
        raise UnsupportedChainError(f"Unsupported chain id: {chain_id}")

    public_default, public_secondary = _PUBLIC_RPC_URLS[chain_id]
    configured = (getattr(Config, attr, None) or "").strip()
    alchemy_key = (getattr(Config, "ALCHEMY_API_KEY", None) or "").strip()

    candidates: list[RpcEndpoint] = []
    if configured and configured != public_default:
        candidates.append(RpcEndpoint(url=configured, source="env"))
    if alchemy_key:
        network = _ALCHEMY_NETWORKS[chain_id]
        candidates.append(
            RpcEndpoint(url=f"https://{network}.g.alchemy.com/v2/{alchemy_key}", source="alchemy")
        )
    candidates.append(RpcEndpoint(url=public_default, source="public"))
    candidates.append(RpcEndpoint(url=public_secondary, source="public"))

    seen: set[str] = set()
    endpoints: list[RpcEndpoint] = []
    for endpoint in candidates:
        if endpoint.url not in seen:
            seen.add(endpoint.url)
            endpoints.append(endpoint)

    now = time.monotonic()
    # Stable sort: healthy endpoints keep precedence order, demoted ones
    # keep theirs behind them.
    return sorted(endpoints, key=lambda e: _demoted_until.get(e.url, 0.0) > now)


def rpc_url_for_chain(chain_id: int) -> str:
    """Return the primary RPC URL for ``chain_id`` (see
    :func:`rpc_endpoints_for_chain` for the precedence).

    Raises:
        UnsupportedChainError: if the chain is not one of the supported EVM
            chains.
    """
    return rpc_endpoints_for_chain(chain_id)[0].url


def is_transport_error(exc: BaseException) -> bool:
    """True when ``exc`` says the *endpoint* failed, not the call.

    Only these are worth retrying on another endpoint. A contract revert must
    never count: it is a property of the call, it would revert identically
    everywhere, and at the raw JSON-RPC layer it looks just like a provider
    error -- so a JSON-RPC error is only treated as transport when its code is
    a known rate-limit code. Anything unrecognised is not transport.
    """
    if isinstance(exc, ContractLogicError):
        return False
    if isinstance(
        exc,
        (
            requests.RequestException,
            OSError,  # ConnectionError, TimeoutError, socket errors
            ProviderConnectionError,
            TooManyRequests,
            TimeExhausted,
            BadResponseFormat,  # the endpoint did not speak JSON-RPC at all
        ),
    ):
        return True
    if isinstance(exc, Web3RPCError):
        rpc_error = (getattr(exc, "rpc_response", None) or {}).get("error") or {}
        return isinstance(rpc_error, dict) and rpc_error.get("code") in _RATE_LIMIT_RPC_CODES
    return False


_URL_RE = re.compile(r"(https?://)(?:[^@/\s'\"]*@)?([^/\s'\"]+)[^\s'\"]*")
# A path-only key segment, as urllib3/requests print it without the host
# ("Max retries exceeded with url: /v2/<key>", '"POST /v2/<key> HTTP/1.1"').
# Alchemy and Infura put the key in /v2/ and /v3/ respectively.
_KEY_PATH_RE = re.compile(r"(/v[23]/)[^\s'\"/?#]+")


def redact_rpc_error(text: str) -> str:
    """``text`` with every URL cut down to scheme and host (no path, no
    userinfo), every ``/v2/<key>``-style path segment masked, and the Alchemy
    key masked wherever it appears.

    A defensive scrub, not the main control: this module never logs or stores
    an RPC exception's text in the first place (see :func:`describe_rpc_error`),
    because requests embeds the full request URL -- and so the key -- in
    HTTPError and ConnectionError messages. This catches anything that still
    gets through, such as web3/urllib3 debug lines (see _SecretScrubFilter).
    """
    redacted = _URL_RE.sub(r"\1\2/***", text)
    redacted = _KEY_PATH_RE.sub(r"\1***", redacted)
    key = (getattr(Config, "ALCHEMY_API_KEY", None) or "").strip()
    if key:
        redacted = redacted.replace(key, "***")
    return redacted


def describe_rpc_error(exc: BaseException) -> str:
    """A loggable description of an RPC failure built only from fields that
    cannot carry a URL: the exception class, the HTTP status, and the
    JSON-RPC error code. **Never** ``str(exc)`` -- requests' messages embed the
    request URL, and an RPC URL's path carries the API key ("401 Client
    Error: Unauthorized for url: https://eth-mainnet.g.alchemy.com/v2/<key>").
    This is the only form an RPC error takes in logs, in
    :class:`ChainReadFailure`, and so in anything built from them."""
    parts = [type(exc).__name__]
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    if isinstance(exc, Web3RPCError):
        rpc_error = (getattr(exc, "rpc_response", None) or {}).get("error")
        if isinstance(rpc_error, dict) and isinstance(rpc_error.get("code"), int):
            parts.append(f"rpc code {rpc_error['code']}")
    return redact_rpc_error(" ".join(parts))


class _SecretScrubFilter(logging.Filter):
    """Scrubs RPC URLs and keys out of records from the HTTP libraries
    underneath web3. web3's HTTPProvider and urllib3 log the full endpoint
    URI at DEBUG; a deploy with LOG_LEVEL=DEBUG would otherwise ship the
    Alchemy key to the log drain and Sentry breadcrumbs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format must not drop the record
            return True
        scrubbed = redact_rpc_error(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = None
        return True


_SCRUBBED_LOGGERS = (
    "web3.providers.HTTPProvider",
    "web3._utils.http_session_manager.HTTPSessionManager",
    "urllib3.connectionpool",
)
for _name in _SCRUBBED_LOGGERS:
    _target = logging.getLogger(_name)
    if not any(isinstance(f, _SecretScrubFilter) for f in _target.filters):
        _target.addFilter(_SecretScrubFilter())


def _demote(endpoint: RpcEndpoint) -> None:
    _demoted_until[endpoint.url] = time.monotonic() + _ENDPOINT_DEMOTION_SECONDS


def reset_endpoint_health() -> None:
    """Forget every endpoint demotion (tests, or an operator after a fix)."""
    _demoted_until.clear()


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

    A chain whose endpoint fails on transport is retried on that chain's
    fallback endpoints first (see :func:`rpc_endpoints_for_chain`). A chain
    that still errors, times out, or is unsupported is recorded
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
            chain_readings = _read_chain(chain_id, checksum_address, chain_tokens)
        except Exception as exc:  # noqa: BLE001 - one bad chain must not sink the rest
            reason = describe_rpc_error(exc)
            logger.warning(
                "Holdings balance read failed for chain %s: %s",
                CHAIN_NAMES.get(chain_id, chain_id),
                reason,
            )
            failures.append(ChainReadFailure(chain_id=chain_id, reason=reason))
            continue

        result_readings.extend(chain_readings)

    return BalanceReadResult(readings=result_readings, failures=failures)


def _read_chain(chain_id: int, checksum_address: str, chain_tokens: list[TokenRef]):
    """Every token on one chain, from the first endpoint that answers.

    A transport failure (see :func:`is_transport_error`) moves on to the
    chain's next endpoint and re-reads the whole chain there; anything else
    -- a revert, a bad registry row -- raises at once, because another
    endpoint would give the same answer. Readings are collected per endpoint
    and only returned on full success, so a mid-chain failure can't leak a
    partial set into the result.
    """
    endpoints = rpc_endpoints_for_chain(chain_id)
    for index, endpoint in enumerate(endpoints):
        try:
            client = _make_web3(chain_id, endpoint.url)
            return [
                BalanceReading(
                    token=token, raw_amount=_read_one_token(client, checksum_address, token)
                )
                for token in chain_tokens
            ]
        except Exception as exc:
            if not is_transport_error(exc) or index == len(endpoints) - 1:
                raise
            _demote(endpoint)
            logger.warning(
                "Holdings RPC (%s) failed for chain %s, retrying on fallback (%s): %s",
                endpoint.source,
                CHAIN_NAMES.get(chain_id, chain_id),
                endpoints[index + 1].source,
                describe_rpc_error(exc),
            )
    raise UnsupportedChainError(f"No RPC endpoint for chain id {chain_id}")  # unreachable


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
