"""Ops alerts for delegated staking.

Delivery and dedupe are the holdings sweep alerts' (src/services/holdings/
alerts.py ``_send``): one Redis ``SET NX EX`` claim per condition per
HOLDINGS_ALERT_COOLDOWN_HOURS so several API instances send one email, an
ERROR log line (the Sentry event) on every alert, and the same ops recipient
resolution. Conditions are namespaced ``delegation_*`` so they never share a
cooldown with a holdings condition.

Alerts carry asset names, counts and dollar totals only -- never a wallet
address, an RPC URL or a key.
"""

from __future__ import annotations

from typing import Any

from src.config.config import Config
from src.services.holdings.alerts import _send


def alert_overspent(asset: str, comparison: dict[str, Any]) -> bool:
    if not Config.DELEGATED_STAKING_ENABLED:
        return False
    return _send(
        f"delegation_overspent_{asset}",
        f"[Gatewayz] Delegated staking {asset.upper()} allowance paused: cost exceeds revenue",
        [
            f"Credits granted for {asset.upper()} delegation cost "
            f"${comparison.get('cost_usd')} against ${comparison.get('revenue_usd')} of "
            f"staking revenue recorded (limit ${comparison.get('limit_usd')}).",
            f"New {asset.upper()} accruals are PAUSED and will stay paused until an admin "
            "calls POST /admin/delegation/resume.",
            "Check GET /admin/delegation/reconciliation: the allowance rate may be above "
            "what the vault fee / pool margin earns, or revenue recording may be failing.",
        ],
    )


def alert_revenue_read_failed(asset: str, error_class: str) -> bool:
    if not Config.DELEGATED_STAKING_ENABLED:
        return False
    return _send(
        f"delegation_revenue_read_failed_{asset}",
        f"[Gatewayz] Delegated staking {asset.upper()} revenue could not be read",
        [
            f"Reconciliation could not record {asset.upper()} revenue ({error_class}). "
            "Revenue is understated until it recovers, so the asset may pause on overspend.",
            "Check the vault RPC (ETHEREUM_RPC_URL / ALCHEMY_API_KEY) or Koios "
            "(KOIOS_BASE_URL / KOIOS_API_KEY) on Railway service api.",
        ],
    )


def alert_if_measured_nothing(summary: dict[str, Any]) -> bool:
    """A sweep that had linked wallets for an asset and recorded none of
    them -- the failure mode that silently zeroed holdings for three days."""
    if not Config.DELEGATED_STAKING_ENABLED or summary.get("skipped"):
        return False
    sent = False
    for asset in ("eth", "ada"):
        block = summary.get(asset) or {}
        if block.get("skipped") == "unconfigured":
            continue
        if int(block.get("wallets") or 0) > 0 and int(block.get("recorded") or 0) == 0:
            reason = block.get("skipped") or (
                f"read_failed={block.get('read_failed', 0)} "
                f"write_failed={block.get('write_failed', 0)}"
            )
            sent = (
                _send(
                    f"delegation_measured_nothing_{asset}",
                    f"[Gatewayz] Delegated staking {asset.upper()} sweep recorded no wallets",
                    [
                        f"The {asset.upper()} measurement sweep at {summary.get('taken_at')} "
                        f"had {block.get('wallets')} linked wallet(s) and recorded none "
                        f"({reason}). Nobody can earn an allowance for a day without "
                        "measurements.",
                    ],
                )
                or sent
            )
    return sent
