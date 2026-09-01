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

    _LOG_SCAN_CHUNK_SIZE = 2000

    def __init__(self, rpc_url: str, contract_address: str):
        self._to_checksum_address = Web3.to_checksum_address
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._contract = self._w3.eth.contract(
            address=self._to_checksum_address(contract_address),
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
            self._to_checksum_address(wallet_address)
        ).call()

    def total_staked(self) -> int:
        return self._contract.functions.totalStaked().call()

    def staked_event_addresses(self, from_block: int, to_block: int) -> list[str]:
        """Distinct staker addresses from Staked events in [from_block, to_block].

        Returns [] (not an error) when from_block > to_block -- the caller's
        range can go empty/invalid after a reorg, and an empty scan is a
        valid, non-exceptional outcome.

        Scans in fixed-size chunks (_LOG_SCAN_CHUNK_SIZE) since public RPC
        endpoints commonly cap the block span of a single eth_getLogs call
        (e.g. ~2048 blocks on Fuji) -- an unchunked scan over a large or
        unbounded range would fail outright.
        """
        if from_block > to_block:
            return []

        stakers: set[str] = set()
        chunk_start = from_block
        while chunk_start <= to_block:
            chunk_end = min(chunk_start + self._LOG_SCAN_CHUNK_SIZE - 1, to_block)
            events = self._contract.events.Staked().get_logs(
                fromBlock=chunk_start, toBlock=chunk_end
            )
            stakers.update(event["args"]["staker"].lower() for event in events)
            chunk_start = chunk_end + 1

        return sorted(stakers)
