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
DEAD = {"UNDETERMINED", "CANCELED", "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT"}

# genlayer-py 0.19 replaced `status_name` with a `lifecycle` object; map it back to these names.
_DECIDED = {
    "accepted": "ACCEPTED",
    "undetermined": "UNDETERMINED",
    "validators_timeout": "VALIDATORS_TIMEOUT",
    "leader_timeout": "LEADER_TIMEOUT",
}
STUDIO_TOP_UP_WEI = 10 * 10**18  # Studio test GEN, minted free by sim_fundAccount


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
        _hex_chain_id_in_gas_estimates(self._client)

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
        """Consensus v0.6: every write carries a fees distribution and the sender pays it;
        without one the consensus contract reverts with FeesDistributionMissing."""
        call = {"address": self.contract, "function_name": "submit_case", "args": args}
        return write_with_fees(self._client, call)

    def tx_state(self, tx: str) -> TxState | None:
        return tx_state_of(self._client, tx)

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
        # None lets genlayer-py resolve the charge for the active decision (topUpAndSubmitAppeal).
        return self._client.appeal_transaction(transaction_id=tx, value=bond_wei)


def write_with_fees(gl_client, call: dict) -> str:
    """write_contract with the fees consensus v0.6 requires. Returns the tx hash as 0x hex.

    Studio simulates the exact call and returns the budget to attach (policy budget as a
    fallback), and a Studio sender is topped up with free test GEN when it runs low."""
    fees = write_fees(gl_client, call)
    tx = gl_client.write_contract(**call, fees=fees)
    return tx if isinstance(tx, str) else "0x" + bytes(tx).hex()


def write_fees(gl_client, call: dict) -> dict:
    from genlayer_py.chains.utils import is_studio_chain

    studio = is_studio_chain(gl_client.chain)
    estimate = None
    if studio:
        try:  # Studio simulates this exact call and returns the budget to attach
            estimate = gl_client.estimate_transaction_fees_for_write(**call)
        except Exception as e:
            logger.info("GenLayer write simulation unavailable, using policy fees: %s", e)
    if estimate is None:
        estimate = gl_client.estimate_transaction_fees({})
    fees = {"distribution": estimate["distribution"], "feeValue": int(estimate["feeValue"])}
    if estimate.get("messageAllocations"):
        fees["messageAllocations"] = estimate["messageAllocations"]
    if studio:
        address = gl_client.local_account.address
        if gl_client.w3.eth.get_balance(address) < 2 * fees["feeValue"]:
            gl_client.fund_account(address, STUDIO_TOP_UP_WEI)
    return fees


def tx_state_of(gl_client, tx: str) -> TxState | None:
    t = gl_client.get_transaction(transaction_hash=tx)
    if t is None:
        return None
    return TxState(
        status=status_name(t),
        result=str(t["result_name"]) if t.get("result_name") is not None else None,
        execution=t.get("tx_execution_result_name") or _leader_rollback(t),
        recipient=str(t.get("to_address") or t.get("recipient") or "") or None,
    )


def leader_error(gl_client, tx: str) -> str | None:
    """The contract's error code when the leader's execution rolled back, else None."""
    t = gl_client.get_transaction(transaction_hash=tx) or {}
    try:
        receipt = t["consensus_data"]["leader_receipt"]
        result = (receipt[0] if isinstance(receipt, list) else receipt)["result"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(result, dict) and result.get("status") == "rollback":
        return str(result.get("payload") or "contract_error")[:300]
    return None


def status_name(t: dict) -> str:
    """Consensus status as PENDING .. ACCEPTED, FINALIZED, from either genlayer-py shape."""
    if t.get("status_name"):  # genlayer-py <= 0.18
        return str(t["status_name"])
    lc = t.get("lifecycle")
    if not isinstance(lc, dict):
        return ""
    state = lc.get("state")
    if state == "processing":
        phase = str(lc.get("phase") or "pending").upper()
        return "PENDING" if phase == "UNINITIALIZED" else phase
    if state == "decided":
        return _DECIDED.get(str(lc.get("outcome")), "UNDETERMINED")
    return {"finalized": "FINALIZED", "canceled": "CANCELED"}.get(str(state), "")


def _leader_rollback(t: dict) -> str | None:
    """Studio leaves the execution result empty when the contract refuses a call (for example
    `submitter_not_allowed`); the refusal is only in the leader receipt. Report it as an error."""
    try:
        receipt = t["consensus_data"]["leader_receipt"]
        result = (receipt[0] if isinstance(receipt, list) else receipt)["result"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(result, dict) and result.get("status") == "rollback":
        return "FINISHED_WITH_ERROR"
    return None


def _hex_chain_id_in_gas_estimates(client) -> None:
    """genlayer-py 0.19.1 puts `chainId` into eth_estimateGas as an int. Studio accepts it;
    Bradbury rejects every write (`cannot unmarshal non-string into ... chainId`). Send hex."""
    make_request = client.provider.make_request

    def patched(method, params, *args, **kwargs):
        if method == "eth_estimateGas" and params and isinstance(params[0], dict):
            chain_id = params[0].get("chainId")
            if isinstance(chain_id, int):
                params = [dict(params[0], chainId=hex(chain_id)), *params[1:]]
        return make_request(method, params, *args, **kwargs)

    client.provider.make_request = patched


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
