"""Thin client for the VerifyJob Intelligent Contract on GenLayer.

Everything chain-specific lives here so routes and the poller can be tested with a
fake. genlayer-py is imported lazily: it needs Python 3.12 and is only exercised
when Verify is configured.

Config (env):
  GENLAYER_NETWORK             studionet | bradbury            (default studionet)
  GENLAYER_VERIFY_CONTRACT     deployed VerifyJob address
  GENLAYER_SUBMITTER_KEY       private key of an allowlisted VerifyJob submitter.
                               Pilot only: production moves this to a managed signer
                               (PRD "Data, privacy and security").
  GENLAYER_FINALITY_WINDOW_S   appeal window used for appeal_window_ends (default 1800;
                               Studionet is 30). An ESTIMATE until the network exposes it.
  VERIFY_CASE_PRICE_USD        flat per-case price used when the network cannot quote
                               fees (Studionet has no fee manager; Bradbury's estimate
                               reverted on 2026-10-05). Default 1.00 (~PRD's $1/decision).
  GEN_USD_PRICE                optional; converts a live GEN fee quote to USD.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

AGREE = {"AGREE", "MAJORITY_AGREE"}
DEAD = {"UNDETERMINED", "CANCELED", "VALIDATORS_TIMEOUT"}


class VerifyUnavailable(RuntimeError):
    """Verify is not configured on this deployment."""


@dataclass
class TxState:
    status: str  # GenLayer status name, e.g. PENDING, ACCEPTED, FINALIZED, UNDETERMINED
    result: str | None  # consensus result name, e.g. MAJORITY_AGREE
    execution: str | None  # FINISHED_WITH_RETURN | FINISHED_WITH_ERROR | None
    recipient: str | None

    @property
    def decided(self) -> bool:
        return self.status in {"ACCEPTED", "FINALIZED", "READY_TO_FINALIZE"}

    @property
    def final(self) -> bool:
        return self.status == "FINALIZED"

    @property
    def dead(self) -> bool:
        return self.status in DEAD or (
            self.decided and self.result is not None and self.result not in AGREE
        )

    @property
    def failed_execution(self) -> bool:
        return self.execution == "FINISHED_WITH_ERROR"


def config() -> dict[str, Any]:
    return {
        "network": os.environ.get("GENLAYER_NETWORK", "studionet"),
        "contract": os.environ.get("GENLAYER_VERIFY_CONTRACT", ""),
        "finality_window_s": int(os.environ.get("GENLAYER_FINALITY_WINDOW_S", "1800")),
        "case_price_usd": Decimal(os.environ.get("VERIFY_CASE_PRICE_USD", "1.00")),
        "gen_usd": (
            Decimal(os.environ["GEN_USD_PRICE"]) if os.environ.get("GEN_USD_PRICE") else None
        ),
    }


def is_configured() -> bool:
    return bool(
        os.environ.get("GENLAYER_VERIFY_CONTRACT") and os.environ.get("GENLAYER_SUBMITTER_KEY")
    )


class GenLayerVerifyClient:
    def __init__(self) -> None:
        if not is_configured():
            raise VerifyUnavailable("GENLAYER_VERIFY_CONTRACT / GENLAYER_SUBMITTER_KEY not set")
        from genlayer_py import create_account, create_client
        from genlayer_py.chains import studionet, testnet_bradbury

        cfg = config()
        chains = {"studionet": studionet, "bradbury": testnet_bradbury}
        if cfg["network"] not in chains:
            raise VerifyUnavailable(f"unknown GENLAYER_NETWORK {cfg['network']!r}")
        self.network = cfg["network"]
        self.contract = cfg["contract"]
        self._client = create_client(
            chain=chains[cfg["network"]],
            account=create_account(os.environ["GENLAYER_SUBMITTER_KEY"]),
        )

    def quote_fee_wei(self) -> int | None:
        """Live network fee quote in GEN wei, or None when the network cannot quote."""
        try:
            r = self._client.estimate_transaction_fees(
                {
                    "leaderTimeunitsAllocation": 100,
                    "validatorTimeunitsAllocation": 200,
                    "rotations": [0],
                }
            )
            return int(dict(r)["feeValue"])
        except Exception as e:
            logger.info("GenLayer fee estimate unavailable on %s: %s", self.network, e)
            return None

    def submit_case(self, args: list) -> str:
        return self._client.write_contract(
            address=self.contract, function_name="submit_case", args=args
        )

    def tx_state(self, tx: str) -> TxState | None:
        t = self._client.get_transaction(transaction_hash=tx)
        if t is None:
            return None
        return TxState(
            status=str(t.get("status_name") or ""),
            result=str(t["result_name"]) if t.get("result_name") is not None else None,
            execution=t.get("tx_execution_result_name"),
            recipient=str(t.get("to_address") or t.get("recipient") or "") or None,
        )

    def read_verdict(self, case_id: str, final: bool) -> dict:
        from genlayer_py.types import TransactionHashVariant

        variant = (
            TransactionHashVariant.LATEST_FINAL if final else TransactionHashVariant.LATEST_NONFINAL
        )
        return dict(
            self._client.read_contract(
                address=self.contract,
                function_name="get_verdict",
                args=[case_id],
                transaction_hash_variant=variant,
            )
        )

    def min_appeal_bond(self, tx: str) -> int | None:
        try:
            return int(self._client.get_min_appeal_bond(tx))
        except Exception as e:
            logger.info("appeal bond unavailable for %s: %s", tx, e)
            return None

    def appeal(self, tx: str, bond_wei: int | None) -> str:
        return self._client.appeal_transaction(transaction_id=tx, value=bond_wei or 0)


_client: GenLayerVerifyClient | None = None


def get_client() -> GenLayerVerifyClient:
    global _client
    if _client is None:
        _client = GenLayerVerifyClient()
    return _client


def quote_usd(client: GenLayerVerifyClient | None) -> dict[str, Any]:
    """Cost shown before submission (PRD Feature 2, R3)."""
    cfg = config()
    fee_wei = client.quote_fee_wei() if client else None
    if fee_wei is not None and cfg["gen_usd"] is not None:
        usd = (Decimal(fee_wei) / Decimal(10**18) * cfg["gen_usd"]).quantize(Decimal("0.000001"))
        return {"usd": str(usd), "fee_gen_wei": str(fee_wei), "source": "network_estimate"}
    return {
        "usd": str(cfg["case_price_usd"]),
        "fee_gen_wei": str(fee_wei) if fee_wei is not None else None,
        "source": "flat_price",
    }
