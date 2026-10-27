"""Read-only StakeWise V3 vault reader.

Reads the Gatewayz-run StakeWise V3 ETH vault at STAKEWISE_VAULT_ADDRESS: a
user's vault shares and what they are worth in ETH, and the vault's fee
settings and fee recipient (which is where our revenue shows up). Never
signs, never sends a transaction.

ABI names verified against stakewise/v3-core main (fc70cbe, release v5.0.1):
``IVaultState.getShares(address) -> uint256``,
``IVaultState.convertToAssets(uint256) -> uint256``,
``IVaultState.totalAssets() -> uint256``,
``IVaultFee.feePercent() -> uint16`` (basis points, 10_000 = 100%) and
``IVaultFee.feeRecipient() -> address``.

RPC endpoints, failover and secret redaction are the holdings reader's
(src/services/holdings/chains.py): same endpoint precedence, same rule that
only a transport failure moves to the next endpoint, and an RPC error is only
ever described by :func:`describe_rpc_error` -- never ``str(exc)``, which
carries the endpoint URL and so the Alchemy key.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from src.config.config import Config
from src.services.holdings.chains import (
    RPC_TIMEOUT_SECONDS,
    _demote,
    describe_rpc_error,
    is_transport_error,
    rpc_endpoints_for_chain,
)

logger = logging.getLogger(__name__)

FEE_PERCENT_DENOMINATOR = 10_000

VAULT_ABI: list[dict[str, Any]] = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "getShares",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "shares", "type": "uint256"}],
        "name": "convertToAssets",
        "outputs": [{"name": "assets", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalAssets",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feePercent",
        "outputs": [{"name": "", "type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "feeRecipient",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


class VaultReadError(Exception):
    """A vault read failed on every endpoint. The message is already
    redacted (describe_rpc_error) and safe to log or store."""


@dataclass(frozen=True)
class VaultInfo:
    fee_percent_bps: int
    fee_recipient: str
    total_assets_wei: int


def vault_address() -> str | None:
    """The configured vault as an EIP-55 address, or None when unset or not
    a valid address -- in which case every ETH reader no-ops."""
    raw = Config.STAKEWISE_VAULT_ADDRESS
    if not raw:
        return None
    try:
        return Web3.to_checksum_address(raw)
    except Exception:  # noqa: BLE001 - a malformed address is "unconfigured"
        logger.warning("STAKEWISE_VAULT_ADDRESS is not a valid address; ETH delegation is off")
        return None


def _make_client(url: str) -> Web3:
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": RPC_TIMEOUT_SECONDS}))


def _call(fn: Callable[[Any], Any]) -> Any:
    """Run one read against the vault, failing over between endpoints on a
    transport error exactly like holdings' _read_chain."""
    address = vault_address()
    if address is None:
        raise VaultReadError("vault not configured")
    chain_id = int(Config.STAKEWISE_VAULT_CHAIN_ID)
    try:
        endpoints = rpc_endpoints_for_chain(chain_id)
    except Exception:  # noqa: BLE001 - unsupported chain id
        raise VaultReadError(f"no RPC for chain {chain_id}") from None

    for index, endpoint in enumerate(endpoints):
        try:
            contract = _make_client(endpoint.url).eth.contract(address=address, abi=VAULT_ABI)
            return fn(contract.functions)
        except Exception as exc:  # noqa: BLE001
            reason = describe_rpc_error(exc)
            if not is_transport_error(exc) or index == len(endpoints) - 1:
                # `from None`: the original exception's text embeds the URL.
                raise VaultReadError(reason) from None
            _demote(endpoint)
            logger.warning(
                "StakeWise vault RPC (%s) failed, retrying on fallback (%s): %s",
                endpoint.source,
                endpoints[index + 1].source,
                reason,
            )
    raise VaultReadError("no RPC endpoint")  # unreachable


def read_shares(wallet_address: str) -> int:
    account = Web3.to_checksum_address(wallet_address)
    return int(_call(lambda f: f.getShares(account).call()))


def convert_to_assets(shares: int) -> int:
    if shares <= 0:
        return 0
    return int(_call(lambda f: f.convertToAssets(int(shares)).call()))


def read_vault_info() -> VaultInfo:
    fee = int(_call(lambda f: f.feePercent().call()))
    recipient = str(_call(lambda f: f.feeRecipient().call()))
    total = int(_call(lambda f: f.totalAssets().call()))
    return VaultInfo(fee_percent_bps=fee, fee_recipient=recipient, total_assets_wei=total)


_FEE_CACHE_SECONDS = 600
_fee_cache: dict[str, Any] = {"at": 0.0, "address": None, "bps": None}


def cached_fee_percent_bps() -> int | None:
    """The vault fee for the public status endpoint, cached for ten minutes
    so an unauthenticated route cannot turn into an RPC amplifier. None when
    unconfigured or unreadable."""
    address = vault_address()
    if address is None:
        return None
    now = time.monotonic()
    if _fee_cache["address"] == address and now - float(_fee_cache["at"]) < _FEE_CACHE_SECONDS:
        return _fee_cache["bps"]
    try:
        bps: int | None = int(_call(lambda f: f.feePercent().call()))
    except VaultReadError as e:
        logger.warning("StakeWise feePercent read failed: %s", e)
        bps = None
    _fee_cache.update({"at": now, "address": address, "bps": bps})
    return bps


def reset_fee_cache() -> None:
    _fee_cache.update({"at": 0.0, "address": None, "bps": None})
