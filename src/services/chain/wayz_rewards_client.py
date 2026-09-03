"""Signing-capable web3.py client for settling WAYZ community-GPU provider
payouts (gatewayz-backend#2266).

Separate from src/services/chain/wayz_token_faucet_client.py (mints new
supply) and src/services/chain/wayz_staking_client.py (read-only) -- this
module holds the `providerRewardsPool` EOA's private key
(Config.WAYZ_REWARDS_POOL_PRIVATE_KEY) and transfers pre-minted supply out
of that pool via ERC20 transfer(), a different trust tier and a different
signing key than the faucet's MINTER_ROLE key. See m4/spec.md §5, §9.
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
_TRANSFER_GAS_LIMIT = 100_000

_transfer_lock = asyncio.Lock()


class WayzProviderRewardsClientError(Exception):
    """Raised when the client can't be constructed (e.g. rewards pool not configured)."""


def _load_abi() -> list[dict]:
    return json.loads(_ABI_PATH.read_text())


class WayzProviderRewardsClient:
    """Signs and sends transfer() transactions out of the providerRewardsPool EOA."""

    def __init__(self, rpc_url: str, contract_address: str, private_key: str):
        self._to_checksum_address = Web3.to_checksum_address
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._account = Account.from_key(private_key)
        self._contract = self._w3.eth.contract(
            address=self._to_checksum_address(contract_address),
            abi=_load_abi(),
        )

    @classmethod
    def from_config(cls) -> WayzProviderRewardsClient:
        if not Config.WAYZ_TOKEN_CONTRACT_ADDRESS or not Config.WAYZ_REWARDS_POOL_PRIVATE_KEY:
            raise WayzProviderRewardsClientError(
                "WAYZ_TOKEN_CONTRACT_ADDRESS or WAYZ_REWARDS_POOL_PRIVATE_KEY is not set"
            )
        return cls(
            Config.AVALANCHE_FUJI_RPC_URL,
            Config.WAYZ_TOKEN_CONTRACT_ADDRESS,
            Config.WAYZ_REWARDS_POOL_PRIVATE_KEY,
        )

    @property
    def pool_address(self) -> str:
        return self._account.address

    async def transfer(self, to_address: str, amount_wei: int) -> str:
        """Transfer amount_wei (already wei-scaled) of WAYZ to to_address.
        Returns the tx hash.

        Serialized by a module-level asyncio.Lock -- same reasoning as
        WayzTokenFaucetClient.mint(): the pool account's on-chain nonce
        would race under concurrent settlement runs otherwise.
        """
        async with _transfer_lock:
            return await asyncio.to_thread(self._transfer_sync, to_address, amount_wei)

    def _transfer_sync(self, to_address: str, amount_wei: int) -> str:
        checksum_to = self._to_checksum_address(to_address)
        nonce = self._w3.eth.get_transaction_count(self._account.address, "pending")
        tx = self._contract.functions.transfer(checksum_to, amount_wei).build_transaction(
            {
                "from": self._account.address,
                "nonce": nonce,
                "chainId": self._w3.eth.chain_id,
                "gas": _TRANSFER_GAS_LIMIT,
                "gasPrice": self._w3.eth.gas_price,
            }
        )
        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.to_0x_hex()

    def pool_balance_wei(self) -> int:
        """Current WAYZ balance (wei) of the rewards pool EOA. Read-only,
        synchronous -- callers on the async path should wrap with
        asyncio.to_thread, matching the settlement job's usage."""
        return self._contract.functions.balanceOf(self._account.address).call()
