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
        self._to_checksum_address = Web3.to_checksum_address
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=self._to_checksum_address(contract_address),
            abi=_load_abi(),
        )

    @classmethod
    def from_config(cls) -> WayzTokenFaucetClient:
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
        checksum_to = self._to_checksum_address(to_address)
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
        return tx_hash.to_0x_hex()
